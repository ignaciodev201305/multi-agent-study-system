"""Estados que intercambian los grafos de contextualización, sesión y documentos."""

from operator import add
from typing import Annotated, List, TypedDict

from langchain_core.messages.base import BaseMessage

from core.reducer import add_optimized
from core.schemas import ContextClassification, MapStudy, MultiAgentSystem

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
    map_study: MapStudy
    rag_context: List[str]
    document_rag_flag: bool
    map_study_flag: bool

class DocumentState(TypedDict):
    session_id: str
    document_id: str
    pdf_path: str
    type: str
    map_study: MapStudy
    rag_context: Annotated[List[str], add]
    document_rag_flag: bool
    map_study_flag: bool
