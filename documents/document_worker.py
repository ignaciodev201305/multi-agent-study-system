"""Workers asíncronos que procesan PDFs encolados en el catálogo."""

import asyncio
import logging
from pathlib import Path
from typing import Any

from config.settings import DATA_DIRECTORY, DOCUMENT_MAX_ATTEMPTS, PDF_DIRECTORY
from documents.document_repository import DocumentRepository

logger = logging.getLogger(__name__)


async def process_document_job(
    document_id: str,
    repository: DocumentRepository,
    graph_document_processor: Any,
) -> None:
    """Reclama un PDF en cola, lo procesa en el grafo y actualiza su estado."""
    pending_document = await repository.get_document(document_id)
    if pending_document is None:
        return

    async with repository.lock_chat(pending_document["chat_id"]):
        document = await repository.claim_queued_document(document_id)
        if document is None:
            return

        pdf_path = (
            Path(DATA_DIRECTORY)
            / document["chat_id"]
            / PDF_DIRECTORY
            / f"{document_id}.pdf"
        )
        graph_input = {
            "session_id": document["chat_id"],
            "document_id": document_id,
            "pdf_path": pdf_path,
            "type": document["document_type"],
        }
        graph_config = {"configurable": {"thread_id": document["chat_id"]}}

        try:
            await graph_document_processor.ainvoke(graph_input, graph_config)
        except asyncio.CancelledError:
            await repository.requeue_document(document_id, DOCUMENT_MAX_ATTEMPTS)
            raise
        except Exception:
            logger.exception(
                "Error procesando documento document_id=%s chat_id=%s",
                document_id,
                document["chat_id"],
            )
            await repository.mark_failed(document_id, "No se pudo procesar el documento.")
        else:
            await repository.mark_ready(document_id)


async def document_worker(
    queue: asyncio.Queue,
    repository: DocumentRepository,
    graph_document_processor: Any,
) -> None:
    """Consume trabajos de la cola continuamente y marca cada tarea como atendida."""
    while True:
        document_id = await queue.get()
        try:
            await process_document_job(document_id, repository, graph_document_processor)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error inesperado en el worker document_id=%s", document_id)
            await repository.mark_failed(document_id, "No se pudo procesar el documento.")
        finally:
            queue.task_done()
