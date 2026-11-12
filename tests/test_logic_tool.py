"""Pruebas de la tool logic: lógica booleana en subproceso, lista blanca y pizarra."""

import json
import unittest
from unittest.mock import patch

from core.tools import TurnContext
from core.tools import logic
from core.tools.logic import logic_tool

# Tope del schema que paga cada turno de una sesión con el área activa.
MAX_SCHEMA_CHARS = 1500


class _Runtime:
    def __init__(self):
        self.context = TurnContext(session_id="s")
        self.events = []

    def stream_writer(self, event):
        self.events.append(event)


class LogicToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = _Runtime()

    async def call(self, **arguments):
        return await logic_tool.coroutine(runtime=self.runtime, **arguments)

    async def test_truth_table_reports_minterms(self):
        result = await self.call(operation="truth_table", expression="A & ~B | C")
        self.assertIn("Variables: A, B, C", result)
        self.assertIn("[1, 3, 4, 5, 7]", result)
        self.assertEqual(self.runtime.events, [])

    async def test_truth_table_goes_to_the_board_when_titled(self):
        result = await self.call(operation="truth_table", expression="A ^ B", board_title="XOR")
        self.assertIn("[[b1]]", result)
        block = self.runtime.context.board.blocks[0]
        self.assertEqual(block["kind"], "table")
        self.assertEqual(block["headers"], ["A", "B", "F"])
        self.assertEqual(block["rows"], [["0", "0", "0"], ["0", "1", "1"], ["1", "0", "1"], ["1", "1", "0"]])
        self.assertEqual(self.runtime.events[0]["block"]["id"], "b1")

    async def test_wide_table_is_not_added_to_the_board(self):
        expression = " | ".join("abcdefg")
        result = await self.call(operation="truth_table", expression=expression, board_title="Grande")
        self.assertIn("hasta 6 variables", result)
        self.assertEqual(self.runtime.context.board.blocks, [])

    async def test_simplify_in_both_forms(self):
        result = await self.call(operation="simplify", expression="(A & B) | (A & ~B) | (~A & B)")
        self.assertIn("Resultado: A | B", result)
        self.assertIn("LaTeX:", result)
        result = await self.call(operation="simplify", expression="(A | B) & (A | ~B)", form="pos")
        self.assertIn("Resultado: A", result)

    async def test_constants_and_implication(self):
        self.assertIn("Resultado: ~A", await self.call(operation="simplify", expression="A ^ 1"))
        self.assertIn("B | ~A", await self.call(operation="simplify", expression="A >> B"))

    async def test_equivalence_and_counterexample(self):
        result = await self.call(operation="equivalent", expression="~(A & B)", other="~A | ~B")
        self.assertIn("Son equivalentes", result)
        result = await self.call(operation="equivalent", expression="A >> B", other="B >> A")
        self.assertIn("No son equivalentes", result)
        self.assertIn("necesita other", await self.call(operation="equivalent", expression="A"))

    async def test_rejects_anything_outside_the_whitelist(self):
        cases = (
            "__import__('os')", "A.b", "f(A)", "A[0]", "A if B else C", "lambda: A", "A; B",
            "A and B", "2", "A ** B", "open(1)", "",
        )
        for expression in cases:
            with self.subTest(expression=expression):
                self.assertIn("Error", await self.call(operation="simplify", expression=expression))

    async def test_rejects_too_many_variables(self):
        expression = " & ".join("abcdefghi")
        self.assertIn("hasta 8 variables", await self.call(operation="simplify", expression=expression))

    async def test_timeout_kills_the_worker(self):
        with patch.object(logic, "TOOL_WORKER_TIMEOUT_SECONDS", 0.01):
            result = await self.call(operation="simplify", expression="A | B")
        self.assertIn("tiempo máximo", result)

    def test_schema_stays_small(self):
        schema = json.dumps(logic_tool.tool_call_schema.model_json_schema(), ensure_ascii=False)
        self.assertLess(len(schema) + len(logic_tool.description), MAX_SCHEMA_CHARS)
        self.assertNotIn("runtime", schema)


if __name__ == "__main__":
    unittest.main()
