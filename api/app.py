"""API FastAPI con el WebSocket de sesión y los endpoints de chats y PDFs."""

import asyncio
import hashlib
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    WebSocketException,
    status,
)
from fastapi.responses import JSONResponse
from langchain.messages import HumanMessage
from langgraph.types import Command

from api.cors import ALLOWED_ORIGINS, ALLOWED_ORIGIN_REGEX, configure_cors
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
from core.astream import execute_astream_graph
from core.graphs import ContextStudyGraph, DocumentProcessorGraph, SessionStudyGraph
from documents.document_cleanup import cleanup_orphan_documents
from documents.document_repository import DocumentRepository
from documents.document_worker import document_worker
from documents.session_file import SessionManager

manager = SessionManager()
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

WS_IDLE_TIMEOUT_SECONDS = 300
WS_LOCK_WAIT_TIMEOUT_SECONDS = 5

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
    app.state.websocket_locks = {}
    app.state.websocket_locks_guard = asyncio.Lock()
    app.state.document_repository = await DocumentRepository.open(APP_DATABASE_PATH)
    app.state.document_queue = asyncio.Queue()
    app.state.document_workers = []

    try:
        app.state.graph_context_study = await ContextStudyGraph().compile_graph()
        app.state.session_study_graph_instance = SessionStudyGraph()
        app.state.graph_session_study = await app.state.session_study_graph_instance.compile_graph()
        app.state.graph_document_processor = await DocumentProcessorGraph().compile_graph()

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
                    app.state.graph_document_processor,
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

        await app.state.document_repository.requeue_processing(DOCUMENT_MAX_ATTEMPTS)
        for graph_name in (
            "graph_context_study",
            "graph_session_study",
            "graph_document_processor",
        ):
            graph = getattr(app.state, graph_name, None)
            if graph is not None:
                await graph.checkpointer.conn.close()
        await app.state.document_repository.close()

app = FastAPI(lifespan=lifespan)

# CORS envuelve al limitador para que también agregue sus encabezados al 413.
app.add_middleware(
    _PdfUploadBodyLimitMiddleware,
    max_body_bytes=MAX_UPLOAD_REQUEST_BYTES,
)
configure_cors(app)


class _WebSocketIdleTimeout(Exception):
    """Señala que el cliente no envió un mensaje dentro del tiempo permitido."""


async def _send_ws_error(
    websocket: WebSocket,
    *,
    code: str,
    content: str,
    retryable: bool,
) -> None:
    """Envía un error con el contrato común del WebSocket si sigue conectado."""
    try:
        await websocket.send_json({
            "type": "error",
            "code": code,
            "content": content,
            "retryable": retryable,
        })
    except (OSError, RuntimeError, WebSocketDisconnect):
        pass


async def _close_websocket(websocket: WebSocket, *, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except (OSError, RuntimeError, WebSocketDisconnect):
        pass


async def _receive_content(websocket: WebSocket) -> str:
    """Espera un mensaje JSON válido, informando payloads incorrectos al cliente."""
    while True:
        try:
            data = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=WS_IDLE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as error:
            raise _WebSocketIdleTimeout from error
        except (json.JSONDecodeError, UnicodeDecodeError):
            await _send_ws_error(
                websocket,
                code="invalid_message",
                content="El mensaje debe ser un JSON válido con un campo 'content' de texto no vacío.",
                retryable=True,
            )
            continue

        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, str) or not content.strip():
            await _send_ws_error(
                websocket,
                code="invalid_message",
                content="El mensaje debe incluir un campo 'content' de texto no vacío.",
                retryable=True,
            )
            continue

        return content.strip()


async def _get_websocket_lock(thread_id: str) -> asyncio.Lock:
    """Obtiene el lock por sesión y registra también las conexiones en espera."""
    async with app.state.websocket_locks_guard:
        entry = app.state.websocket_locks.get(thread_id)
        if entry is None:
            entry = {"lock": asyncio.Lock(), "references": 0}
            app.state.websocket_locks[thread_id] = entry
        entry["references"] += 1
        return entry["lock"]


async def _release_websocket_lock(
    thread_id: str,
    lock: asyncio.Lock,
    *,
    acquired: bool,
) -> None:
    """Libera el lock y elimina su entrada cuando ya no hay conexiones asociadas."""
    if acquired:
        lock.release()

    async with app.state.websocket_locks_guard:
        entry = app.state.websocket_locks.get(thread_id)
        if entry is None or entry["lock"] is not lock:
            return

        entry["references"] -= 1
        if entry["references"] == 0:
            app.state.websocket_locks.pop(thread_id, None)


async def _run_websocket_session(websocket: WebSocket, thread_id: str) -> None:
    """Contextualiza la sesión y procesa sus mensajes mientras el cliente esté conectado."""
    config = {"configurable": {"thread_id": thread_id}}
    graph_context_study = app.state.graph_context_study

    message = await _receive_content(websocket)

    snapshot = await graph_context_study.aget_state(config)
    skip_context = snapshot.values.get("multi_agent_system") is not None

    message_pending = None
    # Si ya existe contexto, conserva el mensaje para el grafo de sesión.
    if skip_context:
        context_study = snapshot.values
        multi_agent_system = snapshot.values["multi_agent_system"]
        message_pending = message
    else:
        # Si hay un interrupt, faltó contexto por parte del usuario.
        graph_input = Command(resume=message) if snapshot.interrupts else {
            "messages": [HumanMessage(content=message)]
        }
        context_study, interrupt_info = await execute_astream_graph(
            graph_context_study,
            websocket,
            graph_input,
            config,
        )

        while interrupt_info:
            await websocket.send_json({
                "type": "human_input_request",
                "content": interrupt_info[0].value,
            })

            message = await _receive_content(websocket)
            context_study, interrupt_info = await execute_astream_graph(
                graph_context_study,
                websocket,
                Command(resume=message),
                config,
            )

        multi_agent_system = context_study.get("multi_agent_system")

    if multi_agent_system is None:
        await _send_ws_error(
            websocket,
            code="context_failed",
            content="No se pudo crear el contexto de estudio. Puedes enviar otro mensaje para intentarlo de nuevo.",
            retryable=True,
        )
        await _close_websocket(
            websocket,
            code=status.WS_1011_INTERNAL_ERROR,
            reason="context_failed",
        )
        return

    if not skip_context:
        await websocket.send_json({
            "type": "context_processed",
            "content": "Contexto procesado",
            "title_session": context_study["classified_context"].title_session,
        })

    while True:
        # Si hay un mensaje pendiente por reconexión, úsalo antes de esperar otro.
        if message_pending is not None:
            message = message_pending
            message_pending = None
        else:
            message = await _receive_content(websocket)

        documents_graph_state = (
            await app.state.graph_document_processor.aget_state(config)
        ).values
        graph_input = {
            "messages": [HumanMessage(content=message)],
            "multi_agent_system_context": multi_agent_system,
            "document_rag_flag": documents_graph_state.get("document_rag_flag", False),
            "rag_context": documents_graph_state.get("rag_context", []),
            "map_study_flag": documents_graph_state.get("map_study_flag", False),
            "map_study": documents_graph_state.get("map_study"),
        }
        logger.info("turn start thread_id=%s", thread_id)
        session_study, _ = await execute_astream_graph(
            app.state.graph_session_study,
            websocket,
            graph_input,
            config,
        )
        logger.info("turn end thread_id=%s", thread_id)
        await websocket.send_json({
            "type": "agent_response",
            "content": session_study["messages"][-1].content,
        })


@app.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """Valida y serializa las conexiones WebSocket de cada sesión."""
    origin = websocket.headers.get("origin")
    origin_allowed = origin in ALLOWED_ORIGINS or (
        origin is not None and re.fullmatch(ALLOWED_ORIGIN_REGEX, origin) is not None
    )
    if not origin_allowed:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="origin_not_allowed",
        )
        return

    await websocket.accept()

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", thread_id):
        await _send_ws_error(
            websocket,
            code="invalid_thread_id",
            content="El identificador de sesión no es válido.",
            retryable=False,
        )
        await _close_websocket(
            websocket,
            code=status.WS_1008_POLICY_VIOLATION,
            reason="invalid_thread_id",
        )
        return

    lock = await _get_websocket_lock(thread_id)
    lock_acquired = False
    try:
        try:
            await asyncio.wait_for(
                lock.acquire(),
                timeout=WS_LOCK_WAIT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await _send_ws_error(
                websocket,
                code="session_busy",
                content="Ya hay una conexión activa para esta sesión. Inténtalo de nuevo.",
                retryable=True,
            )
            logger.warning("ws session_busy thread_id=%s", thread_id)
            await _close_websocket(
                websocket,
                code=status.WS_1013_TRY_AGAIN_LATER,
                reason="session_busy",
            )
            return

        lock_acquired = True
        logger.info("ws lock acquired thread_id=%s", thread_id)
        await _run_websocket_session(websocket, thread_id)
    except WebSocketDisconnect:
        # La desconexión del cliente es un cierre esperado.
        logger.info("ws client closed thread_id=%s", thread_id)
    except _WebSocketIdleTimeout:
        await _send_ws_error(
            websocket,
            code="idle_timeout",
            content="La conexión se cerró por inactividad. Vuelve a conectarte para continuar.",
            retryable=True,
        )
        await _close_websocket(
            websocket,
            code=status.WS_1000_NORMAL_CLOSURE,
            reason="idle_timeout",
        )
    except WebSocketException as error:
        logger.warning(
            "Excepción WebSocket en thread_id=%s (code=%s)",
            thread_id,
            error.code,
        )
        await _send_ws_error(
            websocket,
            code="websocket_error",
            content="Ocurrió un error en la conexión WebSocket.",
            retryable=True,
        )
        await _close_websocket(
            websocket,
            code=status.WS_1011_INTERNAL_ERROR,
            reason="websocket_error",
        )
    except Exception:
        logger.exception("Error procesando la sesión WebSocket thread_id=%s", thread_id)
        await _send_ws_error(
            websocket,
            code="internal_error",
            content="No se pudo procesar el mensaje. Inténtalo de nuevo.",
            retryable=True,
        )
        await _close_websocket(
            websocket,
            code=status.WS_1011_INTERNAL_ERROR,
            reason="internal_error",
        )
    finally:
        await _release_websocket_lock(
            thread_id,
            lock,
            acquired=lock_acquired,
        )
        if lock_acquired:
            logger.info("ws lock released thread_id=%s", thread_id)



@app.delete("/chats/{chat_id}")
async def delete_chat_endpoint(chat_id: str):
    """Elimina los checkpoints y los archivos de una sesión."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", chat_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="chat_id_invalido"
        )

    async with app.state.document_repository.lock_chat(chat_id):
        try:
            for graph in (
                app.state.graph_context_study,
                app.state.graph_session_study,
                app.state.graph_document_processor,
            ):
                await graph.checkpointer.adelete_thread(chat_id)

            session_manager = SessionManager()
            session_manager.session_path = str(Path(DATA_DIRECTORY) / chat_id)
            session_manager.remove_session(chat_id)
            await app.state.document_repository.delete_chat(chat_id)
            app.state.session_study_graph_instance.remove_session_cache_entry(chat_id)
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo eliminar la sesion: " + str(e)
            )

    return {"chat_id": chat_id, "status": "deleted"}


@app.get("/chats/{chat_id}/pdfs")
async def list_chat_pdfs_endpoint(chat_id: str):
    """Lista los documentos de una sesión junto con su estado de procesamiento."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", chat_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="chat_id_invalido",
        )

    documents = await app.state.document_repository.list_for_chat(chat_id)
    return {"chat_id": chat_id, "documents": documents}


@app.post("/chats/{chat_id}/pdfs", status_code=status.HTTP_202_ACCEPTED)
async def upload_endpoint(chat_id: str, file: UploadFile = File(), document_type: Literal["rag", "study_map"] = Form()):
    """Guarda metadata y encola el PDF; el procesamiento continúa en segundo plano."""
    pdf_path: Path | None = None
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", chat_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="chat_id_invalido",
            )
        
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
