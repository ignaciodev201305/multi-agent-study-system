"""Sesión WebSocket: lock por turno, "la última conexión gana" y heartbeat."""

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, WebSocketException, status
from langchain.messages import HumanMessage
from langgraph.types import Command

from api.cors import ALLOWED_ORIGINS, ALLOWED_ORIGIN_REGEX
from api.security import (
    WS_APP_SUBPROTOCOL,
    TurnRateLimiter,
    is_valid_thread_id,
    token_matches,
    websocket_token,
)
from config.settings import WS_MAX_MESSAGE_CHARS
from core.streaming import execute_astream_graph

logger = logging.getLogger(__name__)
router = APIRouter()

WS_IDLE_TIMEOUT_SECONDS = 300
# Un turno largo (tools, handoffs, PDFs recién procesados) puede tardar; se espera
# a que termine el anterior en vez de rechazar la conexión nueva.
TURN_WAIT_TIMEOUT_SECONDS = 120
HEARTBEAT_INTERVAL_SECONDS = 15
WS_CLOSE_SUPERSEDED = 4000


class _WebSocketIdleTimeout(Exception):
    """Señala que el cliente no envió un mensaje dentro del tiempo permitido."""


class _TurnInProgress(Exception):
    """El turno anterior de la sesión no terminó dentro del tiempo de espera."""


class SessionRegistry:
    """Locks por turno y socket activo por thread_id, compartidos por todas las conexiones."""

    def __init__(self):
        self._guard = asyncio.Lock()
        self._locks: dict[str, dict] = {}
        self._sockets: dict[str, WebSocket] = {}
        self.rate_limiter = TurnRateLimiter()

    async def replace_socket(self, thread_id: str, websocket: WebSocket) -> WebSocket | None:
        """Registra la conexión nueva y devuelve la anterior para cerrarla."""
        async with self._guard:
            previous = self._sockets.get(thread_id)
            self._sockets[thread_id] = websocket
            return previous

    async def unregister_socket(self, thread_id: str, websocket: WebSocket) -> None:
        """Quita la conexión del registro solo si sigue siendo la activa."""
        async with self._guard:
            if self._sockets.get(thread_id) is websocket:
                self._sockets.pop(thread_id, None)

    def active_socket(self, thread_id: str) -> WebSocket | None:
        return self._sockets.get(thread_id)

    async def _get_lock(self, thread_id: str) -> asyncio.Lock:
        async with self._guard:
            entry = self._locks.get(thread_id)
            if entry is None:
                entry = {"lock": asyncio.Lock(), "references": 0}
                self._locks[thread_id] = entry
            entry["references"] += 1
            return entry["lock"]

    async def _release_lock(self, thread_id: str, lock: asyncio.Lock, *, acquired: bool) -> None:
        if acquired:
            lock.release()
        async with self._guard:
            entry = self._locks.get(thread_id)
            if entry is None or entry["lock"] is not lock:
                return
            entry["references"] -= 1
            if entry["references"] == 0:
                self._locks.pop(thread_id, None)

    @asynccontextmanager
    async def turn(self, thread_id: str, sender: "SafeSender"):
        """Serializa los turnos de un thread; avisa al cliente si debe esperar."""
        lock = await self._get_lock(thread_id)
        acquired = False
        try:
            waited = lock.locked()
            if waited:
                await sender.send_json({"type": "status", "content": "waiting_previous_turn"})
            try:
                await asyncio.wait_for(lock.acquire(), timeout=TURN_WAIT_TIMEOUT_SECONDS)
            except asyncio.TimeoutError as error:
                raise _TurnInProgress from error
            acquired = True
            logger.info("turn lock acquired thread_id=%s waited=%s", thread_id, waited)
            if waited:
                # La respuesta del turno anterior quedó en el checkpoint: el cliente resincroniza.
                await sender.send_json({"type": "status", "content": "previous_turn_finished"})
            yield
        finally:
            await self._release_lock(thread_id, lock, acquired=acquired)
            if acquired:
                logger.info("turn lock released thread_id=%s", thread_id)


class SafeSender:
    """Envía eventos ignorando fallos: si el cliente se fue, el turno termina y se checkpointea."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.closed = False

    async def send_json(self, payload: dict) -> None:
        if self.closed:
            return
        try:
            await self.websocket.send_json(payload)
        except (OSError, RuntimeError, WebSocketDisconnect):
            self.closed = True


async def _send_ws_error(sender: SafeSender, *, code: str, content: str, retryable: bool) -> None:
    """Envía un error con el contrato común del WebSocket si sigue conectado."""
    await sender.send_json({
        "type": "error",
        "code": code,
        "content": content,
        "retryable": retryable,
    })


async def _close_websocket(websocket: WebSocket, *, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except (OSError, RuntimeError, WebSocketDisconnect):
        pass


async def _receive_content(websocket: WebSocket, sender: SafeSender) -> str:
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
                sender,
                code="invalid_message",
                content="El mensaje debe ser un JSON válido con un campo 'content' de texto no vacío.",
                retryable=True,
            )
            continue

        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, str) or not content.strip():
            await _send_ws_error(
                sender,
                code="invalid_message",
                content="El mensaje debe incluir un campo 'content' de texto no vacío.",
                retryable=True,
            )
            continue

        content = content.strip()
        if len(content) > WS_MAX_MESSAGE_CHARS:
            await _send_ws_error(
                sender,
                code="message_too_long",
                content=f"El mensaje supera el máximo de {WS_MAX_MESSAGE_CHARS} caracteres.",
                retryable=True,
            )
            continue

        return content


async def _heartbeat(sender: SafeSender) -> None:
    """Mantiene vivo el timeout del cliente mientras el grafo trabaja sin emitir tokens."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        await sender.send_json({"type": "status", "content": "working"})


async def _run_graph(graph, sender: SafeSender, graph_input, config):
    heartbeat = asyncio.create_task(_heartbeat(sender))
    try:
        return await execute_astream_graph(graph, sender, graph_input, config)
    finally:
        heartbeat.cancel()


async def _build_documents_input(state, thread_id: str) -> dict:
    """Lee los documentos listos del catálogo; los que terminen a mitad de turno entran en el siguiente."""
    documents = await state.document_repository.list_ready_with_summaries(thread_id)
    return {
        "documents_context": format_documents_context(documents),
        "has_rag": any(document["document_type"] == "rag" for document in documents),
        "has_study_map": any(document["document_type"] == "study_map" for document in documents),
    }


def format_documents_context(documents: list[dict]) -> str:
    """Concatena los resúmenes guardados por el worker, sin llamar al LLM."""
    parts = []
    for document in documents:
        summary = document.get("summary") or {}
        title = summary.get("title") or document["original_filename"]
        if document["document_type"] == "study_map":
            lines = [f"### Mapa de estudio: {title}", summary.get("summary", "")]
            for label, key in (
                ("Temas", "topics"),
                ("Fórmulas", "formulas"),
                ("Ejercicios", "exercises"),
                ("Conceptos", "concepts"),
            ):
                items = summary.get(key) or []
                if items:
                    lines.append(f"{label}:\n" + "\n".join(f"- {item}" for item in items))
        else:
            lines = [f"### Documento consultable (rag): {title}", summary.get("summary", "")]
        parts.append("\n".join(line for line in lines if line))
    return "\n\n".join(parts)


async def _process_message(websocket: WebSocket, sender: SafeSender, thread_id: str, message: str) -> None:
    """Ejecuta un turno: contexto (con sus interrupts) o sesión, según el checkpoint."""
    state = websocket.app.state
    config = {"configurable": {"thread_id": thread_id}}
    graph_context_study = state.graph_context_study

    snapshot = await graph_context_study.aget_state(config)
    multi_agent_system = snapshot.values.get("multi_agent_system")

    if multi_agent_system is None:
        graph_input = Command(resume=message) if snapshot.interrupts else {
            "messages": [HumanMessage(content=message)]
        }
        logger.info("context turn start thread_id=%s", thread_id)
        context_study, interrupt_info = await _run_graph(
            graph_context_study, sender, graph_input, config
        )
        logger.info("context turn end thread_id=%s", thread_id)

        if interrupt_info:
            await sender.send_json({
                "type": "human_input_request",
                "content": interrupt_info[0].value,
            })
            return

        if (context_study or {}).get("multi_agent_system") is None:
            await _send_ws_error(
                sender,
                code="context_failed",
                content="No se pudo crear el contexto de estudio. Puedes enviar otro mensaje para intentarlo de nuevo.",
                retryable=True,
            )
            return

        await sender.send_json({
            "type": "context_processed",
            "content": "Contexto procesado",
            "title_session": context_study["classified_context"].title_session,
        })
        return

    graph_input = {
        "messages": [HumanMessage(content=message)],
        "multi_agent_system_context": multi_agent_system,
        **await _build_documents_input(state, thread_id),
    }
    logger.info("session turn start thread_id=%s", thread_id)
    session_study, _ = await _run_graph(state.graph_session_study, sender, graph_input, config)
    logger.info("session turn end thread_id=%s", thread_id)
    await sender.send_json({
        "type": "agent_response",
        "content": session_study["messages"][-1].content,
    })


async def _run_websocket_session(websocket: WebSocket, sender: SafeSender, thread_id: str) -> None:
    """Procesa mensajes mientras el cliente esté conectado; el lock se toma solo por turno."""
    registry: SessionRegistry = websocket.app.state.session_registry
    while True:
        message = await _receive_content(websocket, sender)
        if not registry.rate_limiter.allow(thread_id):
            await _send_ws_error(
                sender,
                code="rate_limited",
                content="Demasiados mensajes seguidos. Espera un momento antes de enviar otro.",
                retryable=True,
            )
            continue
        try:
            async with registry.turn(thread_id, sender):
                await _process_message(websocket, sender, thread_id, message)
        except _TurnInProgress:
            await _send_ws_error(
                sender,
                code="turn_in_progress",
                content="Todavía se está procesando el mensaje anterior. Inténtalo de nuevo en unos segundos.",
                retryable=True,
            )
        if sender.closed:
            return


def origin_allowed(origin: str | None) -> bool:
    return origin in ALLOWED_ORIGINS or (
        origin is not None and re.fullmatch(ALLOWED_ORIGIN_REGEX, origin) is not None
    )


@router.websocket("/ws/{thread_id}")
async def websocket_endpoint(websocket: WebSocket, thread_id: str):
    """Valida la conexión, reemplaza la anterior del mismo thread y atiende sus turnos."""
    if not origin_allowed(websocket.headers.get("origin")):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="origin_not_allowed",
        )
        return

    if not token_matches(websocket_token(websocket.scope.get("subprotocols", []))):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="unauthorized",
        )
        return

    await websocket.accept(subprotocol=WS_APP_SUBPROTOCOL)
    sender = SafeSender(websocket)

    if not is_valid_thread_id(thread_id):
        await _send_ws_error(
            sender,
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

    registry: SessionRegistry = websocket.app.state.session_registry
    previous = await registry.replace_socket(thread_id, websocket)
    if previous is not None:
        logger.info("ws superseded thread_id=%s", thread_id)
        await _close_websocket(previous, code=WS_CLOSE_SUPERSEDED, reason="superseded")

    try:
        await _run_websocket_session(websocket, sender, thread_id)
    except WebSocketDisconnect:
        # La desconexión del cliente es un cierre esperado.
        logger.info("ws client closed thread_id=%s", thread_id)
    except _WebSocketIdleTimeout:
        await _send_ws_error(
            sender,
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
            sender,
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
            sender,
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
        await registry.unregister_socket(thread_id, websocket)
