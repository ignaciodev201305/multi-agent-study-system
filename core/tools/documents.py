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


def _document_title(document: dict) -> str:
    """Devuelve el título del resumen o, si falta, el nombre del archivo."""
    return (document.get("summary") or {}).get("title") or document["original_filename"]


def _select_documents(documents: list[dict], name: str | None) -> list[dict]:
    """Filtra por una parte del título o del nombre del archivo, sin distinguir mayúsculas."""
    if not name:
        return documents
    needle = name.strip().lower()
    return [
        document for document in documents
        if needle in _document_title(document).lower() or needle in document["original_filename"].lower()
    ]


def _document_not_found(name: str, documents: list[dict]) -> str:
    """Explica que no hay coincidencias y lista los documentos disponibles."""
    available = ", ".join(_document_title(document) for document in documents)
    return f"No hay un documento que coincida con '{name}'. Disponibles: {available}."


@tool
async def search_documents(query: str, runtime: ToolRuntime[TurnContext], document: str | None = None) -> str:
    """Busca fragmentos relevantes en los PDFs que el alumno subió como tipo rag.

    Úsala cuando pregunten por contenido específico de esos PDFs: definiciones, datos,
    ejemplos, ejercicios o citas. La consulta debe ser específica y en lenguaje natural;
    puedes llamarla más de una vez. Devuelve hasta 4 fragmentos con documento y página.
    document limita la búsqueda a un PDF, por parte de su título o nombre de archivo.
    Si no hay resultados, dilo; no inventes contenido.
    """
    context = runtime.context
    rag_documents = [item for item in context.documents if item["document_type"] == "rag"]
    if not rag_documents:
        return "No hay documentos rag indexados en esta sesión."
    selected = _select_documents(rag_documents, document)
    if not selected:
        return _document_not_found(document, rag_documents)
    titles = {item["document_id"]: _document_title(item) for item in rag_documents}
    search_filter = {"session_id": context.session_id}
    if document:
        search_filter = {"$and": [
            search_filter,
            {"document_id": {"$in": [item["document_id"] for item in selected]}},
        ]}

    store = await asyncio.to_thread(_get_vector_store, context.session_id)
    if store is None:
        return "No se pudieron consultar los PDFs en este momento."
    try:
        fragments = await asyncio.to_thread(
            store.similarity_search, query, k=4, filter=search_filter
        )
    except Exception:
        logger.exception("Falló la búsqueda en documentos session_id=%s", context.session_id)
        return "No se pudieron consultar los PDFs en este momento."
    if not fragments:
        return "No se encontraron fragmentos relevantes."

    parts = []
    for fragment in fragments:
        title = titles.get(fragment.metadata.get("document_id"), "documento")
        pages = format_pages(fragment.metadata.get("pages", ""))
        parts.append(f"<fragmento documento=\"{title}\" {pages}>\n{fragment.page_content}\n</fragmento>")
    return "\n\n".join(parts)


@tool
async def get_study_material(
    section: Literal["resumen", "temas", "formulas", "ejercicios", "conceptos"],
    runtime: ToolRuntime[TurnContext],
    number: int | None = None,
    document: str | None = None,
) -> str:
    """Consulta los mapas de estudio de los PDFs del alumno (tipo study_map).

    section elige qué ver: resumen, temas, formulas, ejercicios o conceptos.
    number (desde 1) devuelve solo ese elemento, p. ej. el ejercicio 3.
    document limita la consulta a un mapa, por parte de su título o nombre de archivo.
    """
    key = {
        "resumen": "summary",
        "temas": "topics",
        "formulas": "formulas",
        "ejercicios": "exercises",
        "conceptos": "concepts",
    }[section]
    maps = [
        item for item in runtime.context.documents
        if item["document_type"] == "study_map" and item.get("summary")
    ]
    if not maps:
        return "No hay mapas de estudio en esta sesión."
    selected = _select_documents(maps, document)
    if not selected:
        return _document_not_found(document, maps)

    parts = []
    for item in selected:
        summary = item["summary"]
        title = _document_title(item)
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
