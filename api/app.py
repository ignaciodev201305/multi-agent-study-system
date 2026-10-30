"""API FastAPI: wiring del WebSocket de sesión y endpoints de chats y PDFs."""

import asyncio
import hashlib
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from langchain.messages import AIMessage, HumanMessage

from api.cors import configure_cors
from api.security import TokenAuthMiddleware, validate_chat_id
from api.ws import SessionRegistry, router as ws_router
from config import settings
from config.settings import (
    APP_DATABASE_PATH,
    CHROMA_DIRECTORY,
    DATA_DIRECTORY,
    DOCUMENT_MAX_ATTEMPTS,
    DOCUMENT_WORKERS,
    MAX_DOCUMENTS_PER_CHAT,
    MAX_PDF_BYTES_PER_CHAT,
    MAX_PDF_SIZE_BYTES,
    PDF_DIRECTORY,
    UPLOAD_CHUNK_SIZE,
)
from core.graphs import ContextStudyGraph, SessionStudyGraph
from documents.document_cleanup import cleanup_orphan_documents
from documents.document_repository import DocumentRepository
from documents.document_worker import document_worker
from documents.pipeline import DoclingExecutor, DocumentPipeline
from documents.processor import DocumentIndexer
from documents.session_file import SessionManager

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

UPLOAD_MULTIPART_OVERHEAD_BYTES = 64 * 1024
MAX_UPLOAD_REQUEST_BYTES = MAX_PDF_SIZE_BYTES + UPLOAD_MULTIPART_OVERHEAD_BYTES


class _UploadRequestTooLarge(Exception):
    """Interrumpe el parseo cuando el cuerpo supera el límite de subida."""


async def _send_upload_too_large(scope, receive, send) -> None:
    response = JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content={"detail": "El pdf supera el tamaño maximo"},
    )
    await response(scope, receive, send)


class _PdfUploadBodyLimitMiddleware:
    """Limita el cuerpo multipart antes de que FastAPI parsee o almacene el PDF."""

    def __init__(self, app, *, max_body_bytes: int):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        is_pdf_upload = (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and re.fullmatch(r"/chats/[^/]+/pdfs/?", scope.get("path", "")) is not None
        )
        if not is_pdf_upload:
            await self.app(scope, receive, send)
            return

        content_lengths = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        try:
            declared_lengths = [int(value) for value in content_lengths]
        except ValueError:
            declared_lengths = []
        if declared_lengths and max(declared_lengths) > self.max_body_bytes:
            await _send_upload_too_large(scope, receive, send)
            return

        received_bytes = 0

        async def limited_receive():
            nonlocal received_bytes
            message = await receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    raise _UploadRequestTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _UploadRequestTooLarge:
            await _send_upload_too_large(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Compila los grafos e inicia/recupera el procesamiento asíncrono de PDFs."""
    if not settings.APP_TOKEN:
        raise RuntimeError(
            "Falta APP_TOKEN en el entorno (.env). Genera uno con "
            "'python -c \"import secrets; print(secrets.token_hex(32))\"' y configúralo también en el cliente."
        )
    app.state.session_registry = SessionRegistry()
    app.state.document_repository = await DocumentRepository.open(APP_DATABASE_PATH)
    app.state.document_queue = asyncio.Queue()
    app.state.document_workers = []
    app.state.docling_executor = None

    try:
        app.state.graph_context_study = await ContextStudyGraph().compile_graph()
        app.state.session_study_graph_instance = SessionStudyGraph()
        app.state.graph_session_study = await app.state.session_study_graph_instance.compile_graph()
        app.state.docling_executor = DoclingExecutor()
        document_pipeline = DocumentPipeline(app.state.docling_executor, DocumentIndexer)

        await cleanup_orphan_documents(
            app.state.document_repository,
            Path(DATA_DIRECTORY),
            PDF_DIRECTORY,
            CHROMA_DIRECTORY,
        )
        queued_document_ids = await app.state.document_repository.recover_jobs(
            DOCUMENT_MAX_ATTEMPTS
        )
        for document_id in queued_document_ids:
            app.state.document_queue.put_nowait(document_id)

        app.state.document_workers = [
            asyncio.create_task(
                document_worker(
                    app.state.document_queue,
                    app.state.document_repository,
                    document_pipeline,
                ),
                name=f"document-worker-{index}",
            )
            for index in range(DOCUMENT_WORKERS)
        ]
        yield
    finally:
        for worker in app.state.document_workers:
            worker.cancel()
        if app.state.document_workers:
            await asyncio.gather(*app.state.document_workers, return_exceptions=True)

        if app.state.docling_executor is not None:
            app.state.docling_executor.shutdown()

        await app.state.document_repository.requeue_processing(DOCUMENT_MAX_ATTEMPTS)
        for graph_name in (
            "graph_context_study",
            "graph_session_study",
        ):
            graph = getattr(app.state, graph_name, None)
            if graph is not None:
                await graph.checkpointer.conn.close()
        await app.state.document_repository.close()

app = FastAPI(lifespan=lifespan)

# CORS envuelve al token y al limitador para que también agregue sus encabezados al 401/413.
app.add_middleware(
    _PdfUploadBodyLimitMiddleware,
    max_body_bytes=MAX_UPLOAD_REQUEST_BYTES,
)
app.add_middleware(TokenAuthMiddleware)
configure_cors(app)


app.include_router(ws_router)


@app.get("/chats/{chat_id}/messages")
async def list_chat_messages_endpoint(chat_id: str):
    """Devuelve el estado checkpointeado para que el cliente se resincronice al reconectar."""
    validate_chat_id(chat_id)
    config = {"configurable": {"thread_id": chat_id}}

    context_snapshot = await app.state.graph_context_study.aget_state(config)
    if context_snapshot.values.get("multi_agent_system") is not None:
        phase = "study"
    elif context_snapshot.interrupts:
        phase = "clarification"
    else:
        phase = "setup"
    pending_question = (
        context_snapshot.interrupts[0].value
        if phase == "clarification"
        else None
    )

    session_snapshot = await app.state.graph_session_study.aget_state(config)
    messages = [
        {
            "role": "user" if isinstance(message, HumanMessage) else "assistant",
            "content": message.content,
        }
        for message in session_snapshot.values.get("messages", [])
        if isinstance(message, (HumanMessage, AIMessage)) and isinstance(message.content, str)
    ]
    return {
        "chat_id": chat_id,
        "phase": phase,
        "pending_question": pending_question,
        "messages": messages,
    }


@app.delete("/chats/{chat_id}")
async def delete_chat_endpoint(chat_id: str):
    """Elimina los checkpoints y los archivos de una sesión."""
    validate_chat_id(chat_id)

    # No espera al worker: borrar las filas cancela el documento en curso, que
    # descarta su resultado al no poder guardarlo (ver document_worker).
    try:
        await app.state.document_repository.delete_chat(chat_id)
        for graph in (
            app.state.graph_context_study,
            app.state.graph_session_study,
        ):
            await graph.checkpointer.adelete_thread(chat_id)

        session_manager = SessionManager()
        session_manager.session_path = str(Path(DATA_DIRECTORY) / chat_id)
        session_manager.remove_session(chat_id)
        app.state.session_study_graph_instance.remove_session_cache_entry(chat_id)
        app.state.session_registry.rate_limiter.forget(chat_id)
    except Exception:
        logger.exception("No se pudo eliminar la sesión chat_id=%s", chat_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No se pudo eliminar la sesión.",
        )

    return {"chat_id": chat_id, "status": "deleted"}


@app.get("/chats/{chat_id}/pdfs")
async def list_chat_pdfs_endpoint(chat_id: str):
    """Lista los documentos de una sesión junto con su estado de procesamiento."""
    validate_chat_id(chat_id)

    documents = await app.state.document_repository.list_for_chat(chat_id)
    return {"chat_id": chat_id, "documents": documents}


@app.post("/chats/{chat_id}/pdfs", status_code=status.HTTP_202_ACCEPTED)
async def upload_endpoint(chat_id: str, file: UploadFile = File(), document_type: Literal["rag", "study_map"] = Form()):
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
            reservation, document = await app.state.document_repository.reserve_upload(
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
                app.state.document_queue.put_nowait(document_id)
                response_status = status.HTTP_202_ACCEPTED
            elif document["status"] == "failed":
                async with app.state.document_repository.lock_chat(chat_id):
                    current = await app.state.document_repository.get_document(
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
                        document = await app.state.document_repository.requeue_failed_duplicate(
                            current["document_id"]
                        )
                        if document is not None:
                            app.state.document_queue.put_nowait(current["document_id"])
                        else:
                            document = await app.state.document_repository.get_document(
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
