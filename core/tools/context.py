"""Contexto de un turno que reciben las tools vía ToolRuntime (no lo elige el modelo)."""

from dataclasses import dataclass, field

from core.board import Board


@dataclass
class TurnContext:
    """Datos del turno que comparten el supervisor y sus tools."""
    # Sesión actual; no la elige el modelo.
    session_id: str
    # Documentos listos de la sesión con su resumen.
    documents: list[dict] = field(default_factory=list)
    # Actualizaciones de record_progress durante el turno.
    progress_updates: dict[str, dict] = field(default_factory=dict)
    # Copia de trabajo del turno.
    board: Board = field(default_factory=Board)
    # Quién firma los bloques que se agregan.
    author: str = "profesor_principal"
