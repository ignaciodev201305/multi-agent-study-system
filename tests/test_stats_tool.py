"""Pruebas de la tool stats y del bloque chart que deja en la pizarra."""

import json
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from core.board import Board, BoardError, ChartBlock, add_block, block_details
from core.tools import TurnContext
from core.tools import stats
from core.tools.board import board_add
from core.tools.stats import stats_tool

MAX_SCHEMA_CHARS = 3000


class _Runtime:
    def __init__(self):
        self.context = TurnContext(session_id="s")
        self.events = []

    def stream_writer(self, event):
        self.events.append(event)


class StatsToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = _Runtime()

    async def call(self, **arguments):
        return await stats_tool.coroutine(runtime=self.runtime, **arguments)

    async def test_describe(self):
        result = await self.call(operation="describe", data=[2, 4, 4, 4, 5, 5, 7, 9])
        self.assertIn("media = 5", result)
        self.assertIn("mediana = 4.5", result)
        self.assertIn("desviación estándar muestral = 2.13809", result)
        self.assertIn("IC 95 %", result)

    async def test_distributions(self):
        result = await self.call(operation="distribution", distribution="normal", params=[0, 1], x=1.96, p=0.975)
        self.assertIn("P(X <= x) = 0.975002", result)
        self.assertIn("cuantil de p = 1.95996", result)
        result = await self.call(operation="distribution", distribution="binomial", params=[10, 0.5], x=3)
        self.assertIn("P(X = x) = 0.117188", result)

    async def test_distribution_rejects_bad_parameters(self):
        cases = (
            {"distribution": "normal", "params": [0], "x": 1},
            {"distribution": "normal", "params": [0, -1], "x": 1},
            {"distribution": "normal", "params": [0, 1]},
            {"distribution": "normal", "params": [0, 1], "p": 1.5},
            {"params": [0, 1], "x": 1},
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                self.assertIn("Error", await self.call(operation="distribution", **arguments))

    async def test_tests_of_hypothesis(self):
        result = await self.call(operation="ttest", data=[1, 2, 3, 4, 5], mu=2.5)
        self.assertIn("t = 0.707107, gl = 4", result)
        result = await self.call(operation="ttest", data=[1, 2, 3, 4], data_b=[5, 6, 7, 9])
        self.assertIn("Welch", result)
        self.assertIn("chi2 = 0.4, gl = 2", await self.call(operation="chi2", data=[18, 22, 20]))

    async def test_regression_adds_chart_only_when_titled(self):
        arguments = {"operation": "regression", "data": [1, 2, 3, 4], "data_b": [2.1, 3.9, 6.2, 7.8]}
        result = await self.call(**arguments)
        self.assertIn("y = 1.94 * x + 0.15", result)
        self.assertEqual(self.runtime.context.board.blocks, [])

        result = await self.call(board_title="Ajuste lineal", **arguments)
        self.assertIn("[[b1]]", result)
        block = self.runtime.context.board.blocks[0]
        self.assertEqual(block["kind"], "chart")
        self.assertEqual([series["mark"] for series in block["series"]], ["scatter", "line"])
        self.assertEqual(block["series"][0]["points"][0], [1.0, 2.1])
        self.assertEqual(self.runtime.events[0]["block"], block)

    async def test_histogram_chart_and_limits(self):
        result = await self.call(operation="histogram", data=[1, 2, 2, 3, 3, 3, 4, 4, 5], bins=4, board_title="H")
        self.assertIn("[3, 4): 3", result)
        self.assertEqual(self.runtime.context.board.blocks[0]["series"][0]["mark"], "bar")
        self.assertIn("entre 1 y 50", await self.call(operation="histogram", data=[1, 2], bins=500))

    async def test_long_sample_is_thinned_for_the_chart(self):
        data = [float(index) for index in range(900)]
        await self.call(operation="regression", data=data, data_b=data, board_title="Larga")
        points = self.runtime.context.board.blocks[0]["series"][0]["points"]
        self.assertEqual(len(points), 300)
        self.assertEqual((points[0][0], points[-1][0]), (0.0, 899.0))

    async def test_rejects_oversized_and_invalid_data(self):
        self.assertIn("hasta 2000", await self.call(operation="describe", data=[1.0] * 2001))
        self.assertIn("Error", await self.call(operation="describe"))
        self.assertIn("finitos", await self.call(operation="describe", data=[1.0, float("inf")]))
        self.assertIn("mismo largo", await self.call(operation="regression", data=[1, 2, 3], data_b=[1, 2, 3, 4]))

    async def test_timeout_kills_the_worker(self):
        with patch.object(stats, "TOOL_WORKER_TIMEOUT_SECONDS", 0.01):
            self.assertIn("tiempo máximo", await self.call(operation="describe", data=[1, 2, 3]))

    def test_schema_stays_small(self):
        schema = json.dumps(stats_tool.tool_call_schema.model_json_schema(), ensure_ascii=False)
        self.assertLess(len(schema) + len(stats_tool.description), MAX_SCHEMA_CHARS)
        self.assertNotIn("runtime", schema)


class ChartBlockTests(unittest.TestCase):
    def chart(self, **overrides):
        values = {"kind": "chart", "series": [{"points": [[0, 1], [1, 2]]}]}
        values.update(overrides)
        return ChartBlock.model_validate(values)

    def test_limits(self):
        invalid = (
            {"series": []},
            {"series": [{"points": [[0, 1]]}] * 5},
            {"series": [{"points": [[index, 0] for index in range(301)]}]},
            {"series": [{"points": [[0, float("nan")]]}]},
            {"series": [{"points": [[0, 1]], "mark": "pie"}]},
            {"series": [{"points": [[0, 1]]}], "extra": 1},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.chart(**overrides)

    def test_points_do_not_count_as_content(self):
        board = Board()
        points = [[index, index * 1.234567] for index in range(300)]
        patch_event = add_block(board, self.chart(series=[{"points": points}] * 4), author="a", title="Datos")
        self.assertEqual(patch_event["block"]["series"][0]["points"][1], [1.0, 1.234567])
        details = json.loads(block_details(board, "b1"))
        self.assertNotIn("points", details["series"][0])

    def test_same_chart_twice_is_a_duplicate(self):
        board = Board()
        add_block(board, self.chart(), author="a")
        with self.assertRaises(BoardError):
            add_block(board, self.chart(), author="a")

    def test_chart_is_not_part_of_board_add_schema(self):
        schema = json.dumps(board_add.tool_call_schema.model_json_schema())
        self.assertNotIn("chart", schema)


if __name__ == "__main__":
    unittest.main()
