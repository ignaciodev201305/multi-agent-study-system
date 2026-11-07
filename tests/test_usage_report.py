"""Pruebas del reporte de uso: totales, uso por rol y turnos más pesados."""

import sqlite3
import unittest

from scripts.usage_report import report

ROWS = [
    # chat, turno, rol, modelo, entrada, salida, caché, razonamiento, costo, fecha
    ("c1", "t1", "supervisor", "deepseek/x", 1000, 100, 900, 50, 0.01, "2026-11-01T10:00:00"),
    ("c1", "t1", "supervisor", "deepseek/x", 1000, 100, 900, 50, 0.01, "2026-11-01T10:00:01"),
    ("c1", "t1", "subagent_heavy", "anthropic/y", 500, 200, 0, 100, 0.05, "2026-11-01T10:00:02"),
    ("c2", "t2", "supervisor", "deepseek/x", 2000, 100, 0, 0, None, "2026-11-05T10:00:00"),
]


class UsageReportTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            """CREATE TABLE model_usage (chat_id, turn_id, source, role, model, input_tokens, output_tokens,
               cached_tokens, reasoning_tokens, cost_usd, created_at)"""
        )
        self.connection.executemany(
            "INSERT INTO model_usage VALUES (?, ?, 'turn', ?, ?, ?, ?, ?, ?, ?, ?)", ROWS
        )

    def tearDown(self):
        self.connection.close()

    def test_totals_roles_and_heavy_turns(self):
        text = report(self.connection, None, None, 5)
        self.assertIn("Llamadas: 4", text)
        self.assertIn("Entrada: 4500", text)
        self.assertIn("en caché: 40%", text)
        self.assertIn("Costo: $0.0700", text)
        lines = text.splitlines()
        # El rol más caro va primero; el promedio de entrada es por llamada.
        self.assertTrue(lines[3].startswith("subagent_heavy"))
        supervisor = next(line for line in lines if line.startswith("supervisor"))
        self.assertIn("1333", supervisor)
        turn = next(line for line in lines if line.startswith("t1"))
        self.assertIn("2026-11-01T10:00:00", turn)

    def test_filters_by_chat_and_date(self):
        self.assertIn("Llamadas: 3", report(self.connection, "c1", None, 5))
        self.assertIn("Llamadas: 1", report(self.connection, None, "2026-11-02", 5))
        self.assertEqual(report(self.connection, "nadie", None, 5), "No hay uso registrado con esos filtros.")


if __name__ == "__main__":
    unittest.main()
