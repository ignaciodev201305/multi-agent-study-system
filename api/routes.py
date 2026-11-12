"""Endpoints HTTP de chats: listado, transcripción para resincronizar, borrado y PDFs."""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.security import validate_chat_id
from config.settings import (
    CHAT_MESSAGES_MAX_ENTRIES,
    DATA_DIRECTORY,
    MAX_DOCUMENTS_PER_CHAT,
    MAX_PDF_BYTES_PER_CHAT,
    MAX_PDF_SIZE_BYTES,
    MODEL_ROLES,
    PDF_DIRECTORY,
    UPLOAD_CHUNK_SIZE,
)
from core.board import Board
from core.model_catalog import CatalogUnavailable, filter_models
from core.model_settings import GLOBAL_SCOPE, InvalidModel
from core.tools.documents import forget_vector_store
from core.usage import session_usage
from documents.storage import SessionManager

logger = logging.getLogger(__name__)
router = APIRouter()

# Sesiones que devuelve el listado.
CHATS_LIST_LIMIT = 200


class ModelSelection(BaseModel):
    """Modelo elegido para un rol; null quita el ajuste."""
    role: str
    model: str | None = Field(default=None, min_length=1, max_length=200)


async def _chat_usage(request: Request, chat_id: str) -> dict:
    """Resume el uso de la sesión."""
    return await session_usage(
        request.app.state.usage_repository,
        request.app.state.model_resolver,
        chat_id,
    )


class EffortSelection(BaseModel):
    """Nivel de razonamiento elegido para un rol; null quita el ajuste."""
    role: str
    effort: str | None = Field(default=None, min_length=1, max_length=20)


@router.get("/chats")
async def list_chats_endpoint(request: Request):
    """Lista las sesiones, de la más reciente a la más antigua, con su cantidad de documentos."""
    chats = await request.app.state.chat_repository.list_chats(CHATS_LIST_LIMIT)
    counts = await request.app.state.document_repository.count_by_chat()
    return {"chats": [{**chat, "document_count": counts.get(chat["chat_id"], 0)} for chat in chats]}


@router.get("/chats/{chat_id}/messages")
async def list_chat_messages_endpoint(chat_id: str, request: Request):
    """Devuelve la transcripción y el estado del grafo para que el cliente se resincronice."""
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

    stored = await request.app.state.chat_repository.list_messages(chat_id, CHAT_MESSAGES_MAX_ENTRIES)
    messages = [{**message, "id": str(message["id"])} for message in stored]
    return {
        "chat_id": chat_id,
        "phase": phase,
        "pending_question": pending_question,
        "turn_active": turn_active,
        "messages": messages,
        "board": snapshot.values.get("board") or Board().model_dump(),
        "progress": await request.app.state.progress_repository.list_for_chat(chat_id),
        "usage": await _chat_usage(request, chat_id),
    }


@router.get("/chats/{chat_id}/usage")
async def get_chat_usage_endpoint(chat_id: str, request: Request):
    """Devuelve los tokens, el costo y el contexto usados por una sesión."""
    validate_chat_id(chat_id)
    return {"chat_id": chat_id, **await _chat_usage(request, chat_id)}


@router.get("/chats/{chat_id}/progress")
async def get_chat_progress_endpoint(chat_id: str, request: Request):
    """Devuelve el temario y, por tema evaluado, su estado, repaso y últimas revisiones."""
    validate_chat_id(chat_id)
    config = {"configurable": {"thread_id": chat_id}}

    snapshot = await request.app.state.study_graph.aget_state(config)
    study_plan = snapshot.values.get("study_plan")
    return {
        "chat_id": chat_id,
        "syllabus": study_plan.syllabus if study_plan else [],
        "topics": await request.app.state.progress_repository.list_for_chat(chat_id),
    }


@router.get("/reviews/due")
async def list_due_reviews_endpoint(request: Request, limit: int = Query(default=20, ge=1, le=100)):
    """Lista los repasos vencidos de todas las sesiones."""
    reviews = await request.app.state.progress_repository.list_due(datetime.now(timezone.utc), limit)
    titles = await request.app.state.chat_repository.titles([review["chat_id"] for review in reviews])
    # Los repasos de sesiones sin fila (anteriores a la transcripción) no se muestran.
    return {"reviews": [{**review, "chat_title": titles[review["chat_id"]]} for review in reviews if review["chat_id"] in titles]}


def _check_role(role: str | None) -> None:
    """Rechaza un rol que no exista."""
    if role is not None and role not in MODEL_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Rol de modelo desconocido.",
        )


def _check_session_scope(chat_id: str) -> None:
    """Valida el id y evita que una sesión se confunda con el ámbito global."""
    validate_chat_id(chat_id)
    if chat_id == GLOBAL_SCOPE:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="chat_id_invalido")


async def _save_selection(request: Request, scope: str, selection: ModelSelection) -> None:
    """Valida contra el catálogo y guarda el modelo elegido."""
    _check_role(selection.role)
    if selection.model is not None:
        try:
            await request.app.state.model_resolver.check_model(selection.role, selection.model)
        except InvalidModel as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))
        except CatalogUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No se pudo consultar el catálogo de modelos.",
            )
    await request.app.state.model_settings.set(scope, selection.role, selection.model)


async def _save_effort(request: Request, scope: str, chat_id: str | None, selection: EffortSelection) -> None:
    """Valida el nivel contra el modelo del rol y lo guarda."""
    _check_role(selection.role)
    if selection.effort is not None:
        try:
            await request.app.state.model_resolver.check_effort(chat_id, selection.role, selection.effort)
        except InvalidModel as error:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error))
        except CatalogUnavailable:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="No se pudo consultar el catálogo de modelos.",
            )
    await request.app.state.model_settings.set_effort(scope, selection.role, selection.effort)


@router.get("/models/settings")
async def get_model_settings_endpoint(request: Request):
    """Devuelve el modelo efectivo de cada rol para las sesiones sin ajuste propio."""
    return {"models": await request.app.state.model_resolver.resolve()}


@router.put("/models/settings")
async def update_model_settings_endpoint(selection: ModelSelection, request: Request):
    """Cambia el modelo global de un rol."""
    await _save_selection(request, GLOBAL_SCOPE, selection)
    return {"models": await request.app.state.model_resolver.resolve()}


@router.put("/models/effort")
async def update_global_effort_endpoint(selection: EffortSelection, request: Request):
    """Cambia el nivel de razonamiento global de un rol."""
    await _save_effort(request, GLOBAL_SCOPE, None, selection)
    return {"models": await request.app.state.model_resolver.resolve()}


@router.get("/chats/{chat_id}/models")
async def get_chat_models_endpoint(chat_id: str, request: Request):
    """Devuelve el modelo efectivo de cada rol en una sesión."""
    _check_session_scope(chat_id)
    return {"chat_id": chat_id, "models": await request.app.state.model_resolver.resolve(chat_id)}


@router.put("/chats/{chat_id}/models")
async def update_chat_models_endpoint(chat_id: str, selection: ModelSelection, request: Request):
    """Cambia el modelo de un rol en una sesión; aplica desde el turno siguiente."""
    _check_session_scope(chat_id)
    await _save_selection(request, chat_id, selection)
    return {"chat_id": chat_id, "models": await request.app.state.model_resolver.resolve(chat_id)}


@router.put("/chats/{chat_id}/effort")
async def update_chat_effort_endpoint(chat_id: str, selection: EffortSelection, request: Request):
    """Cambia el nivel de razonamiento de un rol en una sesión; aplica desde el turno siguiente."""
    _check_session_scope(chat_id)
    await _save_effort(request, chat_id, chat_id, selection)
    return {"chat_id": chat_id, "models": await request.app.state.model_resolver.resolve(chat_id)}


@router.get("/models")
async def list_models_endpoint(
    request: Request,
    role: str | None = None,
    q: str = Query(default="", max_length=100),
):
    """Lista los modelos de OpenRouter aptos para un rol."""
    _check_role(role)
    try:
        models = await request.app.state.model_catalog.list_models()
    except CatalogUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No se pudo consultar el catálogo de modelos.",
        )
    return {"models": filter_models(models, role, q)}


@router.delete("/chats/{chat_id}")
async def delete_chat_endpoint(chat_id: str, request: Request):
    """Elimina los checkpoints y los archivos de una sesión."""
    validate_chat_id(chat_id)

    # Borrar las filas cancela el documento en curso sin esperar al worker.
    try:
        await request.app.state.document_repository.delete_chat(chat_id)
        await request.app.state.progress_repository.delete_chat(chat_id)
        await request.app.state.chat_repository.delete_chat(chat_id)
        await request.app.state.model_settings.delete_chat(chat_id)
        await request.app.state.usage_repository.delete_chat(chat_id)
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
