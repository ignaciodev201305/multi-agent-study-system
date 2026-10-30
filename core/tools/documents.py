"""Tools sobre los PDFs de la sesión: búsqueda semántica con páginas y material de estudio."""

import asyncio
import logging
from collections import OrderedDict
from threading import Lock
from typing import Literal

from langchain.tools import ToolRuntime, tool

from core.tools.context import TurnContext
from documents.pipeline import DocumentIndexer

logger = logging.getLogger(__name__)

VECTOR_STORE_CACHE_MAX_ENTRIES = 16
_vector_stores: OrderedDict = OrderedDict()
_vector_stores_lock = Lock()


def _get_vector_store(session_id: str):
    """Chroma + embeddings por sesión, cacheados (LRU) en vez de crearlos en cada llamada."""
    with _vector_stores_lock:
        store = _vector_stores.get(session_id)
        if store is not None:
            _vector_stores.move_to_end(session_id)
            return store
    store = DocumentIndexer().get_vector_store(session_id)
    if store is None:
        return None
    with _vector_stores_lock:
        _vector_stores[session_id] = store
        while len(_vector_stores) > VECTOR_STORE_CACHE_MAX_ENTRIES:
            _vector_stores.popitem(last=False)
    return store


def forget_vector_store(session_id: str) -> None:
    """Se llama al borrar una sesión para no reutilizar un Chroma de un directorio borrado."""
    with _vector_stores_lock:
        _vector_stores.pop(session_id, None)


def format_pages(pages: str) -> str:
    """Convierte las páginas de un fragmento en texto para citar."""
    numbers = [page for page in (pages or "").split(",") if page]
    if not numbers:
        return "página ?"
    if len(numbers) == 1:
        return f"página {numbers[0]}"
    return f"páginas {numbers[0]}-{numbers[-1]}"


@tool
async def search_documents(query: str, runtime: ToolRuntime[TurnContext]) -> str:
    """Busca fragmentos relevantes en los PDFs que el alumno subió como tipo rag.

    Úsala cuando pregunten por contenido específico de esos PDFs: definiciones, datos,
    ejemplos, ejercicios o citas. La consulta debe ser específica y en lenguaje natural;
    puedes llamarla más de una vez. Devuelve hasta 4 fragmentos con documento y página.
    Si no hay resultados, dilo; no inventes contenido.
    """
    context = runtime.context
    titles = {
        document["document_id"]: (document.get("summary") or {}).get("title") or document["original_filename"]
        for document in context.documents
        if document["document_type"] == "rag"
    }
    if not titles:
        return "No hay documentos rag indexados en esta sesión."

    store = await asyncio.to_thread(_get_vector_store, context.session_id)
    if store is None:
        return "No se pudieron consultar los PDFs en este momento."
    try:
        documents = await asyncio.to_thread(
            store.similarity_search, query, k=4, filter={"session_id": context.session_id}
        )
    except Exception:
        logger.exception("Falló la búsqueda en documentos session_id=%s", context.session_id)
        return "No se pudieron consultar los PDFs en este momento."
    if not documents:
        return "No se encontraron fragmentos relevantes."

    parts = []
    for document in documents:
        title = titles.get(document.metadata.get("document_id"), "documento")
        pages = format_pages(document.metadata.get("pages", ""))
        parts.append(f"<fragmento documento=\"{title}\" {pages}>\n{document.page_content}\n</fragmento>")
    return "\n\n".join(parts)


@tool
async def get_study_material(
    section: Literal["resumen", "temas", "formulas", "ejercicios", "conceptos"],
    runtime: ToolRuntime[TurnContext],
    number: int | None = None,
) -> str:
    """Consulta los mapas de estudio de los PDFs del alumno (tipo study_map).

    section elige qué ver: resumen, temas, formulas, ejercicios o conceptos.
    number (desde 1) devuelve solo ese elemento, p. ej. el ejercicio 3.
    """
    key = {
        "resumen": "summary",
        "temas": "topics",
        "formulas": "formulas",
        "ejercicios": "exercises",
        "conceptos": "concepts",
    }[section]
    maps = [
        document for document in runtime.context.documents
        if document["document_type"] == "study_map" and document.get("summary")
    ]
    if not maps:
        return "No hay mapas de estudio en esta sesión."

    parts = []
    for document in maps:
        summary = document["summary"]
        title = summary.get("title") or document["original_filename"]
        value = summary.get(key)
        if not value:
            parts.append(f"<material documento=\"{title}\">Sin {section}.</material>")
            continue
        if isinstance(value, str):
            body = value
        elif number is not None:
            if not 1 <= number <= len(value):
                body = f"No existe el elemento {number}; hay {len(value)}."
            else:
                body = f"{number}. {value[number - 1]}"
        else:
            body = "\n".join(f"{index}. {item}" for index, item in enumerate(value, start=1))
        parts.append(f"<material documento=\"{title}\" seccion=\"{section}\">\n{body}\n</material>")
    return "\n\n".join(parts)
