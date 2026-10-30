"""Endpoints HTTP de chats: historial para resincronizar, borrado y PDFs."""

import hashlib
import logging
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from langchain.messages import AIMessage, HumanMessage

from api.security import validate_chat_id
from config.settings import (
    DATA_DIRECTORY,
    MAX_DOCUMENTS_PER_CHAT,
    MAX_PDF_BYTES_PER_CHAT,
    MAX_PDF_SIZE_BYTES,
    PDF_DIRECTORY,
    UPLOAD_CHUNK_SIZE,
)
from core.board import Board
from core.tools.documents import forget_vector_store
from documents.storage import SessionManager

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/chats/{chat_id}/messages")
async def list_chat_messages_endpoint(chat_id: str, request: Request):
    """Devuelve el estado checkpointeado para que el cliente se resincronice al reconectar."""
    validate_chat_id(chat_id)
    config = {"configurable": {"thread_id": chat_id}}

    # Se lee antes del checkpoint: si dice que terminó, el estado ya trae la respuesta.
    turn_active = request.app.state.session_registry.is_turn_active(chat_id)
    snapshot = await request.app.state.study_graph.aget_state(config)
    if snapshot.values.get("study_plan") is not None:
        phase = "study"
    elif snapshot.interrupts:
        phase = "clarification"
    else:
        phase = "setup"
    pending_question = snapshot.interrupts[0].value if phase == "clarification" else None

    messages = [
        {
            "role": "user" if isinstance(message, HumanMessage) else "assistant",
            "content": message.content,
        }
        for message in snapshot.values.get("messages", [])
        if isinstance(message, (HumanMessage, AIMessage)) and isinstance(message.content, str)
    ]
    return {
        "chat_id": chat_id,
        "phase": phase,
        "pending_question": pending_question,
        "turn_active": turn_active,
        "messages": messages,
        "board": snapshot.values.get("board") or Board().model_dump(),
    }


@router.delete("/chats/{chat_id}")
async def delete_chat_endpoint(chat_id: str, request: Request):
    """Elimina los checkpoints y los archivos de una sesión."""
    validate_chat_id(chat_id)

    # Borrar las filas cancela el documento en curso sin esperar al worker.
    try:
        await request.app.state.document_repository.delete_chat(chat_id)
        await request.app.state.study_graph.checkpointer.adelete_thread(chat_id)

        session_manager = SessionManager()
        session_manager.session_path = str(Path(DATA_DIRECTORY) / chat_id)
        session_manager.remove_session(chat_id)
        request.app.state.study_graph_instance.session.remove_session_cache_entry(chat_id)
        forget_vector_store(chat_id)
        request.app.state.session_registry.rate_limiter.forget(chat_id)
    except Exception:
        logger.exception("No se pudo eliminar la sesión chat_id=%s", chat_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo eliminar la sesión.",
        )

    return {"chat_id": chat_id, "status": "deleted"}


@router.get("/chats/{chat_id}/pdfs")
async def list_chat_pdfs_endpoint(chat_id: str, request: Request):
    """Lista los documentos de una sesión junto con su estado de procesamiento."""
    validate_chat_id(chat_id)

    documents = await request.app.state.document_repository.list_for_chat(chat_id)
    return {"chat_id": chat_id, "documents": documents}


@router.post("/chats/{chat_id}/pdfs", status_code=status.HTTP_202_ACCEPTED)
async def upload_endpoint(
    chat_id: str,
    request: Request,
    file: UploadFile = File(),
    document_type: Literal["rag", "study_map"] = Form(),
):
    """Guarda metadata y encola el PDF; el procesamiento continúa en segundo plano."""
    pdf_path: Path | None = None
    try:
        validate_chat_id(chat_id)
        
        if file.content_type not in {"application/pdf", "application/x-pdf"}:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Solo se aceptan archivos PDF",
            )
        
        # Verificación básica de que el binario comienza como PDF.
        header = await file.read(1024)
        await file.seek(0)

        if b"%PDF-" not in header:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El archivo no parece ser un PDF",
            )
        
        document_id = uuid4().hex
        session_directory = Path(DATA_DIRECTORY) / chat_id / PDF_DIRECTORY
        session_directory.mkdir(parents=True, exist_ok=True)
        pdf_path = session_directory / f"{document_id}.pdf"
        total_bytes = 0
        digest = hashlib.sha256()

        try:
            with pdf_path.open("wb") as destination:
                while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                    total_bytes += len(chunk)
                    if total_bytes > MAX_PDF_SIZE_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                            detail="El pdf supera el tamaño maximo",
                        )
                    destination.write(chunk)
                    digest.update(chunk)

            original_filename = file.filename or "document.pdf"
            reservation, document = await request.app.state.document_repository.reserve_upload(
                document_id=document_id,
                chat_id=chat_id,
                original_filename=original_filename,
                document_type=document_type,
                size_bytes=total_bytes,
                sha256=digest.hexdigest(),
                max_documents=MAX_DOCUMENTS_PER_CHAT,
                max_bytes=MAX_PDF_BYTES_PER_CHAT,
            )

            if reservation == "document_quota":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"La sesión alcanzó el límite de {MAX_DOCUMENTS_PER_CHAT} PDFs.",
                )
            if reservation == "byte_quota":
                quota_mib = MAX_PDF_BYTES_PER_CHAT // (1024 * 1024)
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"La sesión superaría el límite total de {quota_mib} MiB.",
                )

            duplicate = reservation == "duplicate"
            if not duplicate:
                request.app.state.document_queue.put_nowait(document_id)
                response_status = status.HTTP_202_ACCEPTED
            elif document["status"] == "failed":
                async with request.app.state.document_repository.lock_chat(chat_id):
                    current = await request.app.state.document_repository.get_document(
                        document["document_id"]
                    )
                    if current is None:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail="La sesión cambió mientras se subía el documento. Inténtalo de nuevo.",
                        )
                    if current["status"] == "failed":
                        existing_pdf = session_directory / f"{current['document_id']}.pdf"
                        pdf_path.replace(existing_pdf)
                        document = await request.app.state.document_repository.requeue_failed_duplicate(
                            current["document_id"]
                        )
                        if document is not None:
                            request.app.state.document_queue.put_nowait(current["document_id"])
                        else:
                            document = await request.app.state.document_repository.get_document(
                                current["document_id"]
                            )
                    else:
                        pdf_path.unlink(missing_ok=True)
                        document = current

                if document is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="No se pudo recuperar el documento existente.",
                    )
                response_status = (
                    status.HTTP_200_OK
                    if document["status"] == "ready"
                    else status.HTTP_202_ACCEPTED
                )
            else:
                pdf_path.unlink(missing_ok=True)
                response_status = (
                    status.HTTP_200_OK
                    if document["status"] == "ready"
                    else status.HTTP_202_ACCEPTED
                )
        except HTTPException:
            pdf_path.unlink(missing_ok=True)
            raise
        except Exception as error:
            pdf_path.unlink(missing_ok=True)
            logger.exception("No se pudo guardar o encolar el PDF chat_id=%s", chat_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo registrar el PDF para procesamiento",
            ) from error
        return JSONResponse(
            status_code=response_status,
            content={
                "chat_id": document["chat_id"],
                "document_id": document["document_id"],
                "document_type": document["document_type"],
                "original_filename": document["original_filename"],
                "size_bytes": document["size_bytes"],
                "sha256": document["sha256"],
                "status": document["status"],
                "chunks": document["chunks"],
                "error": document["error"],
                "duplicate": duplicate,
            },
        )
    finally:
        await file.close()
