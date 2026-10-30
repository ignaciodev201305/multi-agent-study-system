"""Pruebas del calendario de repasos: transiciones por caja y estado."""

import unittest
from datetime import datetime, timedelta, timezone

from core.study.scheduler import REVIEW_INTERVAL_DAYS, next_review

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


class NextReviewTests(unittest.TestCase):
    def test_transitions(self):
        for box, status, expected_box, days in (
            (0, "dominado", 1, 3),
            (1, "dominado", 2, 7),
            (2, "dominado", 3, 14),
            (3, "dominado", 4, 30),
            (4, "dominado", 4, 30),
            (0, "en_progreso", 0, 1),
            (2, "en_progreso", 2, 7),
            (4, "en_progreso", 4, 30),
            (0, "con_dificultad", 0, 1),
            (3, "con_dificultad", 0, 1),
            (4, "con_dificultad", 0, 1),
        ):
            with self.subTest(box=box, status=status):
                self.assertEqual(
                    next_review(box, status, NOW),
                    (expected_box, NOW + timedelta(days=days)),
                )

    def test_intervals_grow(self):
        self.assertEqual(REVIEW_INTERVAL_DAYS, (1, 3, 7, 14, 30))


if __name__ == "__main__":
    unittest.main()
