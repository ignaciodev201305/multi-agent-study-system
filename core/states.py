"""Estados de los grafos de contextualización y de sesión."""

from operator import add
from typing import Annotated, List, TypedDict

from langchain_core.messages.base import BaseMessage
from langgraph.graph.message import add_messages

from core.schemas import ContextClassification, StudyPlan


def merge_progress(current: dict | None, updates: dict | None) -> dict:
    """El progreso de un tema se reemplaza por su evaluación más reciente."""
    return {**(current or {}), **(updates or {})}


class ContextStudyState(TypedDict):
    messages: Annotated[List[BaseMessage], add]
    classified_context: ContextClassification
    study_plan: StudyPlan
    clarification_round: int
    remaining_rounds: int

class SessionState(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    study_plan: StudyPlan
    summary: str
    # Documentos ready con su resumen; los carga la API al empezar cada turno.
    documents: list[dict]
    # Modelo del alumno: tema -> {"status", "evidence"}; lo escribe la tool record_progress.
    progress: Annotated[dict, merge_progress]
