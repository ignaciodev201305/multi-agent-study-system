"""Pruebas de la tool units: conversión, análisis dimensional y lista blanca."""

import json
import unittest
from unittest.mock import patch

from core.tools import units
from core.tools.units import units_tool

MAX_SCHEMA_CHARS = 1200


class UnitsToolTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, **arguments):
        return await units_tool.ainvoke(arguments)

    async def test_conversions(self):
        self.assertIn("3000 pascal", await self.call(value=3, from_unit="kN/m^2", to_unit="Pa"))
        self.assertIn("25 meter / second", await self.call(value=90, from_unit="km/h", to_unit="m/s"))
        self.assertIn("298.15 kelvin", await self.call(value=25, from_unit="degC", to_unit="K"))

    async def test_without_target_goes_to_si_with_dimension(self):
        result = await self.call(value=1, from_unit="kN*m")
        self.assertIn("1000 kilogram * meter ** 2 / second ** 2 (SI)", result)
        self.assertIn("[mass] * [length] ** 2 / [time] ** 2", result)

    async def test_incompatible_dimensions_are_explained(self):
        result = await self.call(value=1, from_unit="m", to_unit="s")
        self.assertIn("Dimensiones incompatibles", result)
        self.assertIn("[length]", result)
        self.assertIn("[time]", result)

    async def test_unknown_unit(self):
        self.assertIn("Unidad desconocida", await self.call(value=1, from_unit="florp", to_unit="s"))

    async def test_rejects_anything_outside_the_whitelist(self):
        for unit in ('__import__("os")', "m; s", "m[0]", "lambda: 1", "m" * 61, "", "m = 3", "a.b_c"):
            with self.subTest(unit=unit):
                self.assertIn("Error", await self.call(value=1, from_unit=unit, to_unit="m"))
                self.assertIn("Error", await self.call(value=1, from_unit="m", to_unit=unit or "{}"))

    async def test_timeout_kills_the_worker(self):
        with patch.object(units, "TOOL_WORKER_TIMEOUT_SECONDS", 0.01):
            self.assertIn("tiempo máximo", await self.call(value=1, from_unit="m", to_unit="cm"))

    def test_schema_stays_small(self):
        schema = json.dumps(units_tool.tool_call_schema.model_json_schema(), ensure_ascii=False)
        self.assertLess(len(schema) + len(units_tool.description), MAX_SCHEMA_CHARS)


if __name__ == "__main__":
    unittest.main()
