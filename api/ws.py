"""Sesión WebSocket: lock por turno, "la última conexión gana" y heartbeat."""

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

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
from core.agents.catalog import ARCHETYPES
from core.chats import ChatRepository
from core.streaming import execute_astream_graph, token_text
from core.study.repository import ProgressRepository
from core.usage import UsageTracker, session_usage

logger = logging.getLogger(__name__)
router = APIRouter()

WS_IDLE_TIMEOUT_SECONDS = 300
# Tiempo máximo de espera a que termine el turno anterior.
TURN_WAIT_TIMEOUT_SECONDS = 120
HEARTBEAT_INTERVAL_SECONDS = 15
WS_CLOSE_SUPERSEDED = 4000
# Etiquetas de la transcripción y aviso que cierra la preparación.
SUPERVISOR_AGENT = "Supervisor"
PREPARATION_AGENT = "Preparación"
CONTEXT_APPROVED_TEXT = "Contexto aprobado. El equipo de tutores está listo."
USAGE_EVENT_FIELDS = ("context_used", "context_limit", "total_tokens", "cost_usd", "cost_estimated", "cost_unknown")


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
        """Devuelve la conexión activa de una sesión."""
        return self._sockets.get(thread_id)

    def is_turn_active(self, thread_id: str) -> bool:
        """Indica si la sesión tiene un turno en curso."""
        entry = self._locks.get(thread_id)
        return entry is not None and entry["lock"].locked()

    async def _get_lock(self, thread_id: str) -> asyncio.Lock:
        """Obtiene el lock por sesión y registra quién lo usa."""
        async with self._guard:
            entry = self._locks.get(thread_id)
            if entry is None:
                entry = {"lock": asyncio.Lock(), "references": 0}
                self._locks[thread_id] = entry
            entry["references"] += 1
            return entry["lock"]

    async def _release_lock(self, thread_id: str, lock: asyncio.Lock, *, acquired: bool) -> None:
        """Libera el lock y elimina su entrada cuando ya no hay conexiones asociadas."""
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
                # Avisa al cliente para recuperar la respuesta del turno anterior.
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
        """Envía el evento y marca la conexión como cerrada si falla."""
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


async def _receive_content(websocket: WebSocket, sender: SafeSender) -> tuple[str, bool]:
    """Espera un mensaje JSON válido y devuelve su texto y si pide el modo profundo."""
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

        deep = data.get("deep", False)
        if not isinstance(deep, bool):
            await _send_ws_error(
                sender,
                code="invalid_message",
                content="El campo 'deep' debe ser true o false.",
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

        return content, deep


async def _heartbeat(sender: SafeSender) -> None:
    """Mantiene vivo el timeout del cliente mientras el grafo trabaja sin emitir tokens."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        await sender.send_json({"type": "status", "content": "working"})


async def _run_graph(graph, sender: SafeSender, graph_input, config):
    """Ejecuta el grafo manteniendo el heartbeat mientras dura el turno."""
    heartbeat = asyncio.create_task(_heartbeat(sender))
    try:
        return await execute_astream_graph(graph, sender, graph_input, config)
    finally:
        heartbeat.cancel()


async def _save_progress(repository: ProgressRepository, thread_id: str, result: dict) -> None:
    """Guarda las evaluaciones del turno; un fallo no debe perder la respuesta ya generada."""
    try:
        await repository.apply_updates(
            thread_id,
            result.get("progress_updates") or [],
            datetime.now(timezone.utc),
        )
    except Exception:
        logger.exception("No se pudo guardar el progreso thread_id=%s", thread_id)


async def _save_message(repository: ChatRepository, thread_id: str, role: str, content: str, **fields) -> None:
    """Guarda un mensaje de la transcripción; un fallo no debe romper el turno."""
    try:
        await repository.add_message(thread_id, role, content, datetime.now(timezone.utc), **fields)
    except Exception:
        logger.exception("No se pudo guardar la transcripción thread_id=%s", thread_id)


class _TurnUsage:
    """Guarda el uso de un turno a medida que ocurre y se lo informa al cliente."""

    def __init__(self, state, sender: SafeSender, thread_id: str):
        self.state = state
        self.sender = sender
        self.thread_id = thread_id
        self.turn_id = uuid4().hex
        self.tracker = UsageTracker(
            catalog=state.model_resolver.catalog,
            on_record=self.flush,
            relay_tools=frozenset(ARCHETYPES),
        )
        self._saved = 0
        self._saved_tools = 0
        self._lock = asyncio.Lock()

    async def flush(self, _record=None) -> None:
        """Guarda lo pendiente y envía el resumen; un fallo no debe afectar al turno."""
        try:
            async with self._lock:
                pending_tools = self.tracker.tool_records[self._saved_tools:]
                if pending_tools:
                    await self.state.usage_repository.add_tool_records(self.thread_id, self.turn_id, pending_tools)
                    self._saved_tools += len(pending_tools)
                pending = self.tracker.records[self._saved:]
                if not pending:
                    return
                await self.state.usage_repository.add_records(self.thread_id, self.turn_id, pending)
                self._saved += len(pending)
            summary = await session_usage(
                self.state.usage_repository,
                self.state.model_resolver,
                self.thread_id,
            )
            await self.sender.send_json({
                "type": "usage",
                "content": "",
                **{field: summary[field] for field in USAGE_EVENT_FIELDS},
            })
        except Exception:
            logger.exception("No se pudo guardar el uso thread_id=%s", self.thread_id)

    async def save_relay(self, answer: str) -> None:
        """Mide cuánto de la respuesta copió el supervisor de sus especialistas; un fallo no afecta al turno."""
        try:
            await self.state.usage_repository.add_relay(
                self.thread_id, self.turn_id, self.tracker.relay_sources, answer
            )
        except Exception:
            logger.exception("No se pudo medir el relevo thread_id=%s", self.thread_id)


async def _process_message(
    websocket: WebSocket, sender: SafeSender, thread_id: str, message: str, deep: bool = False
) -> None:
    """Ejecuta un turno del grafo padre; él decide si contextualiza o estudia."""
    state = websocket.app.state
    # Va en la configuración porque un subgrafo reanudado no recibe el estado nuevo del padre.
    models = await state.model_resolver.resolve(thread_id)
    usage = _TurnUsage(state, sender, thread_id)
    config = {
        # El modo profundo solo lo usa el turno de estudio; el contexto y sus interrupciones lo ignoran.
        "configurable": {"thread_id": thread_id, "models": models, "deep_mode": deep},
        "callbacks": [usage.tracker],
    }
    graph = state.study_graph

    # Se guarda antes del grafo: si el cliente recarga a mitad del turno, su mensaje ya está.
    await _save_message(state.chat_repository, thread_id, "user", message)
    snapshot = await graph.aget_state(config)
    had_plan = snapshot.values.get("study_plan") is not None
    # Usa los documentos listos al empezar el turno.
    graph_input = Command(resume=message) if snapshot.interrupts else {
        "messages": [HumanMessage(content=message)],
        "documents": await state.document_repository.list_ready_with_summaries(thread_id),
        "progress": await state.progress_repository.list_for_chat(thread_id),
    }

    logger.info(
        "turn start thread_id=%s deep=%s models=%s",
        thread_id,
        deep,
        {role: entry["model"] for role, entry in models.items()},
    )
    try:
        result, interrupt_info = await _run_graph(graph, sender, graph_input, config)
    finally:
        # Un turno fallido también gastó.
        await usage.flush()
    logger.info("turn end thread_id=%s", thread_id)

    if interrupt_info:
        await _save_message(
            state.chat_repository, thread_id, "assistant", interrupt_info[0].value,
            agent=PREPARATION_AGENT, phase="clarification",
        )
        await sender.send_json({
            "type": "human_input_request",
            "content": interrupt_info[0].value,
        })
    elif had_plan:
        # Se guarda antes de avisar, porque el cliente refresca el progreso con la respuesta.
        await _save_progress(state.progress_repository, thread_id, result)
        answer = result["messages"][-1].content
        await _save_message(
            state.chat_repository, thread_id, "assistant", token_text(answer),
            agent=SUPERVISOR_AGENT, phase="study",
        )
        await sender.send_json({
            "type": "agent_response",
            "content": answer,
        })
        await usage.save_relay(token_text(answer))
    elif (result or {}).get("study_plan") is None:
        await _send_ws_error(
            sender,
            code="context_failed",
            content="No se pudo crear el contexto de estudio. Puedes enviar otro mensaje para intentarlo de nuevo.",
            retryable=True,
        )
    else:
        title = result["classified_context"].title_session
        await _save_message(
            state.chat_repository, thread_id, "system", CONTEXT_APPROVED_TEXT,
            phase="study", title=(title or "").strip() or None,
        )
        await sender.send_json({
            "type": "context_processed",
            "content": "Contexto procesado",
            "title_session": title,
        })


async def _run_websocket_session(websocket: WebSocket, sender: SafeSender, thread_id: str) -> None:
    """Procesa mensajes mientras el cliente esté conectado; el lock se toma solo por turno."""
    registry: SessionRegistry = websocket.app.state.session_registry
    while True:
        message, deep = await _receive_content(websocket, sender)
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
                await _process_message(websocket, sender, thread_id, message, deep)
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
    """Indica si el Origin pertenece a los orígenes permitidos."""
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
