"""Registro de tools: los arquetipos y el supervisor piden sus tools por nombre."""

from core.tools.board import board_add, board_edit, get_board
from core.tools.context import TurnContext
from core.tools.documents import get_study_material, search_documents
from core.tools.progress import record_progress
from core.tools.symbolic import math_tool
from core.tools.web import build_web_search_tool

_STATIC_TOOLS = {
    "math": math_tool,
    "search_documents": search_documents,
    "get_study_material": get_study_material,
    "record_progress": record_progress,
    "board_add": board_add,
    "board_edit": board_edit,
    "get_board": get_board,
}

# Nombres válidos en el catálogo; web_search se crea al pedirla.
TOOL_NAMES = frozenset({*_STATIC_TOOLS, "web_search"})


def build_tools(names) -> list:
    """Instancia las tools pedidas; web_search se crea al pedirla (necesita TAVILY_API_KEY)."""
    tools = []
    for name in names:
        if name == "web_search":
            tools.append(build_web_search_tool())
        else:
            tools.append(_STATIC_TOOLS[name])
    return tools


__all__ = ["TOOL_NAMES", "TurnContext", "build_tools"]
