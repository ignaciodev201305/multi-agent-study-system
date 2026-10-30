"""Calendario de repasos con el sistema Leitner."""

from datetime import datetime, timedelta

REVIEW_INTERVAL_DAYS = (1, 3, 7, 14, 30)


def next_review(box: int, status: str, now: datetime) -> tuple[int, datetime]:
    """Devuelve la caja que corresponde a la evaluación y la fecha del próximo repaso."""
    if status == "dominado":
        box = min(box + 1, len(REVIEW_INTERVAL_DAYS) - 1)
    elif status == "con_dificultad":
        box = 0
    return box, now + timedelta(days=REVIEW_INTERVAL_DAYS[box])
