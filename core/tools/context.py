"""Contexto de un turno que reciben las tools vía ToolRuntime (no lo elige el modelo)."""

from dataclasses import dataclass, field

from core.board import Board
from core.schemas import StudyPlan
from core.study.repository import ProgressUpdate


@dataclass
class TurnContext:
    """Datos del turno que comparten el supervisor y sus tools."""
    # Sesión actual; no la elige el modelo.
    session_id: str
    # Documentos listos de la sesión con su resumen.
    documents: list[dict] = field(default_factory=list)
    # Plan con el temario de la sesión.
    study_plan: StudyPlan | None = None
    # Evaluaciones de record_progress durante el turno.
    progress_updates: list[ProgressUpdate] = field(default_factory=list)
    # Copia de trabajo del turno.
    board: Board = field(default_factory=Board)
    # Quién firma los bloques que se agregan.
    author: str = "profesor_principal"
