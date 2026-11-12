"""Pruebas de la tool numeric: EDOs, raíces, mínimos e integrales con la lista blanca de math."""

import json
import math
import unittest
from unittest.mock import patch

from core.agents.catalog import AREAS
from core.tools import TurnContext
from core.tools import numeric
from core.tools.numeric import numeric_tool

MAX_SCHEMA_CHARS = 3000


class _Runtime:
    def __init__(self):
        self.context = TurnContext(session_id="s")
        self.events = []

    def stream_writer(self, event):
        self.events.append(event)


class NumericToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = _Runtime()

    async def call(self, **arguments):
        return await numeric_tool.coroutine(runtime=self.runtime, **arguments)

    async def test_harmonic_oscillator(self):
        arguments = {
            "operation": "ode", "expressions": ["v", "-x"], "variables": ["x", "v"],
            "initial": [1, 0], "span": [0, math.pi],
        }
        result = await self.call(**arguments)
        self.assertIn("- x = -1 (mínimo -1, máximo 1)", result)
        self.assertEqual(self.runtime.context.board.blocks, [])

        result = await self.call(board_title="Oscilador", **arguments)
        self.assertIn("[[b1]]", result)
        block = self.runtime.context.board.blocks[0]
        self.assertEqual([series["label"] for series in block["series"]], ["x", "v"])
        self.assertEqual(len(block["series"][0]["points"]), 200)
        self.assertEqual(block["x_label"], "t")

    async def test_ode_uses_time(self):
        result = await self.call(
            operation="ode", expressions=["-2*t*y"], variables=["y"], initial=[1], span=[0, 1],
        )
        self.assertIn("y = 0.367879", result)

    async def test_roots(self):
        result = await self.call(operation="root", expressions=["x^2 = 2"], variables=["x"], initial=[1])
        self.assertIn("x = 1.41421", result)
        result = await self.call(
            operation="root", expressions=["x + y = 3", "x*y = 2"], variables=["x", "y"], initial=[0, 5],
        )
        self.assertIn("x = 1, y = 2", result)

    async def test_minimize_with_and_without_bounds(self):
        result = await self.call(
            operation="minimize", expressions=["(x-2)^2 + (y+1)^2"], variables=["x", "y"], initial=[0, 0],
        )
        self.assertIn("x = 2, y = -1", result)
        result = await self.call(
            operation="minimize", expressions=["(x-2)^2"], variables=["x"], initial=[0], bounds=[[-1, 1]],
        )
        self.assertIn("x = 1", result)

    async def test_definite_integral(self):
        result = await self.call(operation="integrate", expressions=["x^2"], variables=["x"], span=[0, 3])
        self.assertIn("Integral = 9", result)

    async def test_expressions_use_the_math_whitelist(self):
        for expression in ("__import__('os')", "x.real", "open(1)", "x[0]", "f(x)"):
            with self.subTest(expression=expression):
                result = await self.call(operation="integrate", expressions=[expression], variables=["x"], span=[0, 1])
                self.assertIn("Error", result)

    async def test_rejects_invalid_input(self):
        cases = (
            ({"operation": "root", "expressions": ["x + z"], "variables": ["x"], "initial": [1]}, "sin declarar"),
            ({"operation": "root", "expressions": ["x"], "variables": ["x", "y"], "initial": [1, 1]}, "una por variable"),
            ({"operation": "root", "expressions": ["x"], "variables": ["x"], "initial": [1, 2]}, "necesita 1"),
            ({"operation": "root", "expressions": ["x"], "variables": ["os.path"], "initial": [1]}, "no válido"),
            ({"operation": "root", "expressions": ["x"] * 5, "variables": ["x"], "initial": [1]}, "de 1 a 4"),
            ({"operation": "ode", "expressions": ["t"], "variables": ["t"], "initial": [1], "span": [0, 1]}, "tiempo"),
            ({"operation": "ode", "expressions": ["y"], "variables": ["y"], "initial": [1], "span": [1, 0]}, "menor"),
            ({"operation": "integrate", "expressions": ["x"], "variables": ["x"]}, "span"),
            ({"operation": "minimize", "expressions": ["x^2"], "variables": ["x"], "initial": [0], "bounds": [[1, 0]]}, "menor"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                self.assertIn(message, await self.call(**arguments))

    async def test_timeout_kills_the_worker(self):
        with patch.object(numeric, "TOOL_WORKER_TIMEOUT_SECONDS", 0.01):
            result = await self.call(operation="integrate", expressions=["x"], variables=["x"], span=[0, 1])
        self.assertIn("tiempo máximo", result)

    def test_schema_stays_small(self):
        schema = json.dumps(numeric_tool.tool_call_schema.model_json_schema(), ensure_ascii=False)
        self.assertLess(len(schema) + len(numeric_tool.description), MAX_SCHEMA_CHARS)
        self.assertNotIn("runtime", schema)

    def test_areas_with_numeric(self):
        self.assertIn("numeric", AREAS["datos_ia"].tools)
        self.assertIn("numeric", AREAS["mecanica_civil"].tools)
        self.assertNotIn("numeric", AREAS["informatica"].tools)


if __name__ == "__main__":
    unittest.main()
