"""Tools de búsqueda web."""

from langchain_tavily import TavilySearch


def build_web_tools():
    """Devuelve las herramientas de búsqueda web (Tavily)."""
    return [TavilySearch()]
