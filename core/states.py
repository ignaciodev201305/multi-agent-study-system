"""Estados de los grafos de contextualización y de sesión."""

from typing import Annotated, List, TypedDict

from langchain_core.messages.base import BaseMessage
from langgraph.graph.message import add_messages

from core.schemas import ContextClassification, StudyPlan


def merge_progress(current: dict | None, updates: dict | None) -> dict:
    """El progreso de un tema se reemplaza por su evaluación más reciente."""
    return {**(current or {}), **(updates or {})}


class ContextStudyState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    classified_context: ContextClassification
    study_plan: StudyPlan
    clarification_round: int

class StudyState(ContextStudyState):
    """Estado único de la sesión: contexto y turnos comparten thread y checkpointer."""
    summary: str
    # Documentos listos de la sesión, cargados al empezar cada turno.
    documents: list[dict]
    # Progreso del alumno por tema.
    progress: Annotated[dict, merge_progress]
    # Pizarra de la sesión como JSON.
    board: dict
