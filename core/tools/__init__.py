"""Registro de tools: cada agente pide sus herramientas al ToolsBuilder."""

from core.tools.documents import build_document_tools
from core.tools.web import build_web_tools


class ToolsBuilder:
    """
    Las funciones se encargan de retornar la lista de tools para un agente
    """
    def get_tavily_tools(self):
        """Devuelve las herramientas de búsqueda web para el clasificador de contexto."""
        return build_web_tools()

    def get_vector_store_tools(self, session_id):
        """Devuelve la tool de búsqueda semántica limitada a los PDFs de la sesión."""
        return build_document_tools(session_id)
