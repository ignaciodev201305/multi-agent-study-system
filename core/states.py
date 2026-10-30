"""Estados de los grafos de contextualización y de sesión."""

from operator import add
from typing import Annotated, List, TypedDict

from langchain_core.messages.base import BaseMessage

from core.reducer import add_optimized
from core.schemas import ContextClassification, MultiAgentSystem

class ContextStudyState(TypedDict):
    messages: Annotated[List[BaseMessage], add]
    classified_context: ContextClassification
    multi_agent_system: MultiAgentSystem
    clarification_round: int
    remaining_rounds: int

class SessionState(TypedDict):
    messages: Annotated[List[BaseMessage], add_optimized]
    multi_agent_system_context: MultiAgentSystem
    summarize_conversation: str
    documents_context: str
    has_rag: bool
    has_study_map: bool
