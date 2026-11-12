"""Pruebas de la tool beam: vigas con solución conocida, validación y diagramas en la pizarra."""

import json
import unittest
from unittest.mock import patch

from core.tools import TurnContext
from core.tools import beam
from core.tools.beam import beam_tool

MAX_SCHEMA_CHARS = 3000


class _Runtime:
    def __init__(self):
        self.context = TurnContext(session_id="s")
        self.events = []

    def stream_writer(self, event):
        self.events.append(event)


class BeamToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = _Runtime()

    async def call(self, **arguments):
        return await beam_tool.coroutine(runtime=self.runtime, **arguments)

    async def test_simply_supported_with_centered_load(self):
        result = await self.call(length=4, supports="simply_supported", point_loads=[[2, 10]])
        self.assertIn("fuerza en x = 0: 5", result)
        self.assertIn("fuerza en x = 4: 5", result)
        self.assertIn("Cortante máximo: 5", result)
        self.assertIn("Momento flector máximo (positivo tracciona abajo): 10 en x = 2", result)
        self.assertEqual(self.runtime.context.board.blocks, [])

    async def test_cantilever_with_tip_load_and_deflection(self):
        result = await self.call(length=3, supports="cantilever", point_loads=[[3, 10]], ei=1000)
        self.assertIn("fuerza en x = 0: 10", result)
        self.assertIn("momento en x = 0: 30", result)
        self.assertIn("-30 en x = 0", result)
        # P·L³ / (3·E·I) = 10·27 / 3000
        self.assertIn("Flecha máxima (negativa hacia abajo): -0.09 en x = 3", result)

    async def test_uniform_load_and_fixed_ends(self):
        result = await self.call(length=4, supports="simply_supported", distributed_loads=[[0, 4, 2]], ei=100)
        self.assertIn("4 en x = 2", result)
        self.assertIn("-0.0666667 en x = 2", result)
        result = await self.call(length=6, supports="fixed_fixed", distributed_loads=[[0, 6, 3]])
        # q·L² / 12 en los empotramientos
        self.assertIn("momento en x = 0: 9", result)
        self.assertIn("momento en x = 6: -9", result)

    async def test_overhang_triangular_load_and_point_moment(self):
        result = await self.call(length=6, supports="simply_supported", support_positions=[0, 4], point_loads=[[6, 5]])
        self.assertIn("fuerza en x = 0: -2.5", result)
        self.assertIn("fuerza en x = 4: 7.5", result)
        result = await self.call(length=3, supports="simply_supported", distributed_loads=[[0, 3, 0, 6]])
        self.assertIn("fuerza en x = 0: 3", result)
        self.assertIn("fuerza en x = 3: 6", result)
        result = await self.call(length=4, supports="simply_supported", moments=[[2, 8]])
        self.assertIn("fuerza en x = 0: -2", result)
        self.assertNotIn("inf", result)

    async def test_diagrams_go_to_the_board_when_titled(self):
        result = await self.call(length=4, supports="simply_supported", point_loads=[[2, 10]], board_title="Viga 1")
        self.assertIn("[[b1]]", result)
        self.assertIn("[[b2]]", result)
        blocks = self.runtime.context.board.blocks
        self.assertEqual([block["title"] for block in blocks], ["Viga 1: cortante", "Viga 1: momento flector"])
        self.assertEqual({block["kind"] for block in blocks}, {"chart"})
        shear = dict(map(tuple, blocks[0]["series"][0]["points"]))
        self.assertEqual((shear[1.0], shear[3.0]), (5.0, -5.0))
        self.assertLessEqual(len(blocks[0]["series"][0]["points"]), 300)

    async def test_rejects_invalid_input(self):
        cases = (
            ({"point_loads": None}, "al menos una carga"),
            ({"point_loads": [[9, 1]]}, "fuera de la viga"),
            ({"point_loads": [[1, 1]] * 11}, "hasta 10"),
            ({"point_loads": [[1]]}, "lleva 2"),
            ({"point_loads": [[1, float("nan")]]}, "finitos"),
            ({"distributed_loads": [[3, 1, 2]]}, "inicio debe ser menor"),
            ({"point_loads": [[1, 1]], "support_positions": [2, 2]}, "distintas"),
            ({"point_loads": [[1, 1]], "ei": 0}, "mayor que 0"),
        )
        for arguments, message in cases:
            with self.subTest(arguments=arguments):
                result = await self.call(length=4, supports="simply_supported", **arguments)
                self.assertIn(message, result)
        self.assertIn("length", await self.call(length=0, supports="cantilever", point_loads=[[0, 1]]))

    async def test_timeout_kills_the_worker(self):
        with patch.object(beam, "TOOL_WORKER_TIMEOUT_SECONDS", 0.01):
            result = await self.call(length=4, supports="simply_supported", point_loads=[[2, 10]])
        self.assertIn("tiempo máximo", result)

    def test_schema_stays_small(self):
        schema = json.dumps(beam_tool.tool_call_schema.model_json_schema(), ensure_ascii=False)
        self.assertLess(len(schema) + len(beam_tool.description), MAX_SCHEMA_CHARS)
        self.assertNotIn("runtime", schema)


if __name__ == "__main__":
    unittest.main()
