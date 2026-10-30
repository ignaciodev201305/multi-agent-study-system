"""Registro de tools: los arquetipos y el supervisor piden sus tools por nombre."""

from core.tools.context import TurnContext
from core.tools.documents import get_study_material, search_documents
from core.tools.progress import record_progress
from core.tools.symbolic import math_tool
from core.tools.web import build_web_search_tool, build_web_tools

_STATIC_TOOLS = {
    "math": math_tool,
    "search_documents": search_documents,
    "get_study_material": get_study_material,
    "record_progress": record_progress,
}


def build_tools(names) -> list:
    """Instancia las tools pedidas; web_search se crea al pedirla (necesita TAVILY_API_KEY)."""
    tools = []
    for name in names:
        if name == "web_search":
            tools.append(build_web_search_tool())
        else:
            tools.append(_STATIC_TOOLS[name])
    return tools


class ToolsBuilder:
    """
    Las funciones se encargan de retornar la lista de tools para un agente
    """
    def get_tavily_tools(self):
        """Devuelve las herramientas de búsqueda web para el clasificador de contexto."""
        return build_web_tools()


__all__ = ["TurnContext", "ToolsBuilder", "build_tools"]
