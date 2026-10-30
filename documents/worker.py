"""Workers asíncronos que procesan PDFs encolados en el catálogo."""

import asyncio
import logging
from pathlib import Path

from config.settings import DATA_DIRECTORY, DOCUMENT_MAX_ATTEMPTS, PDF_DIRECTORY
from documents.repository import DocumentRepository
from documents.pipeline import DocumentIndexer, DocumentPipeline

logger = logging.getLogger(__name__)


async def process_document_job(
    document_id: str,
    repository: DocumentRepository,
    pipeline: DocumentPipeline,
) -> None:
    """Reclama un PDF en cola, lo procesa y guarda su resultado en el catálogo."""
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

        try:
            summary, chunks = await pipeline.process(document, pdf_path)
        except asyncio.CancelledError:
            await repository.requeue_document(document_id, DOCUMENT_MAX_ATTEMPTS)
            raise
        except asyncio.TimeoutError:
            logger.warning("Docling superó el tiempo máximo document_id=%s", document_id)
            await repository.mark_failed(document_id, "El documento tardó demasiado en procesarse.")
            return
        except Exception:
            logger.exception(
                "Error procesando documento document_id=%s chat_id=%s",
                document_id,
                document["chat_id"],
            )
            await repository.mark_failed(document_id, "No se pudo procesar el documento.")
            return

        saved = await repository.mark_ready(document_id, summary, chunks)
        if not saved and await repository.get_document(document_id) is None:
            # La sesión se borró durante el procesamiento: se descartan los vectores escritos.
            logger.info("Documento descartado por sesión eliminada document_id=%s", document_id)
            if document["document_type"] == "rag":
                await asyncio.to_thread(_delete_session_vectors, document["chat_id"])


def _delete_session_vectors(chat_id: str) -> None:
    try:
        DocumentIndexer().delete_orphan_vectors(chat_id, set())
    except Exception:
        logger.exception("No se pudieron borrar los vectores de chat_id=%s", chat_id)


async def document_worker(
    queue: asyncio.Queue,
    repository: DocumentRepository,
    pipeline: DocumentPipeline,
) -> None:
    """Consume trabajos de la cola continuamente y marca cada tarea como atendida."""
    while True:
        document_id = await queue.get()
        try:
            await process_document_job(document_id, repository, pipeline)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Error inesperado en el worker document_id=%s", document_id)
            await repository.mark_failed(document_id, "No se pudo procesar el documento.")
        finally:
            queue.task_done()
