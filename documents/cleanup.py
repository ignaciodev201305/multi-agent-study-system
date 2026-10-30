"""Limpieza idempotente de PDFs y vectores sin fila de catálogo."""

import logging
from pathlib import Path

from documents.repository import DocumentRepository
from documents.pipeline import DocumentIndexer

logger = logging.getLogger(__name__)


async def cleanup_orphan_documents(
    repository: DocumentRepository,
    data_directory: Path,
    pdf_directory: str,
    chroma_directory: str,
) -> dict[str, int]:
    """Borra PDFs y vectores sin registro y metadata cuyo PDF ya no existe.

    Devuelve el número de metadata, archivos y vectores huérfanos retirados.
    """
    if not data_directory.exists():
        return {"missing_rows": 0, "orphan_files": 0, "orphan_vectors": 0}

    rows = await repository.list_all()
    document_ids_by_chat: dict[str, set[str]] = {}
    for row in rows:
        document_ids_by_chat.setdefault(row["chat_id"], set()).add(row["document_id"])

    missing_rows = 0
    for row in rows:
        expected_pdf = (
            data_directory
            / row["chat_id"]
            / pdf_directory
            / f"{row['document_id']}.pdf"
        )
        if expected_pdf.is_file():
            continue
        await repository.delete_document(row["document_id"])
        document_ids_by_chat[row["chat_id"]].discard(row["document_id"])
        missing_rows += 1
        logger.info(
            "Se eliminó metadata sin archivo document_id=%s chat_id=%s",
            row["document_id"],
            row["chat_id"],
        )

    orphan_files = 0
    orphan_vectors = 0
    for session_directory in data_directory.iterdir():
        if not session_directory.is_dir():
            continue

        chat_id = session_directory.name
        valid_document_ids = document_ids_by_chat.get(chat_id, set())
        pdf_session_directory = session_directory / pdf_directory
        if pdf_session_directory.is_dir():
            for pdf_path in pdf_session_directory.glob("*.pdf"):
                if pdf_path.stem in valid_document_ids:
                    continue
                pdf_path.unlink(missing_ok=True)
                orphan_files += 1

        chroma_session_directory = session_directory / chroma_directory
        if not chroma_session_directory.is_dir():
            continue

        try:
            indexer = DocumentIndexer()
            orphan_vectors += indexer.delete_orphan_vectors(chat_id, valid_document_ids)
        except Exception:
            logger.exception("No se pudo limpiar vectores huérfanos chat_id=%s", chat_id)

    logger.info(
        "Limpieza B-27: metadata sin PDF=%s, PDFs huérfanos=%s, vectores huérfanos=%s",
        missing_rows,
        orphan_files,
        orphan_vectors,
    )
    return {
        "missing_rows": missing_rows,
        "orphan_files": orphan_files,
        "orphan_vectors": orphan_vectors,
    }
