"""Pruebas del WebSocket: lock por turno, supersede, espera y heartbeat (grafos simulados)."""

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain.messages import AIMessage
from starlette.websockets import WebSocketDisconnect

from api import ws
from api.ws import SessionRegistry
from core.graphs import format_documents_context

# TestClient muta el dict de headers: cada conexión recibe una copia (dict(ORIGIN)).
ORIGIN = {"origin": "http://127.0.0.1:5173"}
TOKEN = "token-de-prueba"
PROTOCOLS = ["study.v1", f"token.{TOKEN}"]


class _FakeStudyGraph:
    """Simula turnos lentos y registra cuántos corren a la vez."""

    def __init__(self, delay, context_ready):
        self.delay = delay
        self.context_ready = context_ready
        self.active = 0
        self.max_active = 0
        self.calls = []
        self.inputs = []
        self.configs = []
        self.progress_updates = []

    async def aget_state(self, config):
        values = {"study_plan": "plan"} if self.context_ready else {}
        return SimpleNamespace(values=values, interrupts=())

    async def astream(self, graph_input, config, stream_mode, subgraphs):
        if not self.context_ready:
            yield (), "values", {"__interrupt__": [SimpleNamespace(value="¿Qué nivel tienes?")]}
            return
        content = graph_input["messages"][-1].content
        self.calls.append(content)
        self.inputs.append(graph_input)
        self.configs.append(config)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        yield (), "values", {
            "messages": [AIMessage(content=f"respuesta:{content}")],
            "progress_updates": self.progress_updates,
        }


class _FakeRepository:
    async def list_ready_with_summaries(self, chat_id):
        return []


class _FakeResolver:
    catalog = None

    async def resolve(self, chat_id=None):
        return {"supervisor": {"model": "acme/modelo"}}


class _FakeProgressRepository:
    def __init__(self):
        self.saved = []
        self.broken = False

    async def list_for_chat(self, chat_id):
        return [{"topic_number": 1, "chat_id": chat_id}]

    async def apply_updates(self, chat_id, updates, now):
        if self.broken:
            raise RuntimeError("base no disponible")
        self.saved.append((chat_id, updates, now))


def _make_client(test, *, delay=0.0, context_ready=True):
    app = FastAPI()
    app.include_router(ws.router)
    app.state.session_registry = SessionRegistry()
    app.state.study_graph = _FakeStudyGraph(delay, context_ready)
    app.state.document_repository = _FakeRepository()
    app.state.progress_repository = _FakeProgressRepository()
    app.state.model_resolver = _FakeResolver()
    app.state.usage_repository = None
    token_patch = patch("config.settings.APP_TOKEN", TOKEN)
    token_patch.start()
    test.addCleanup(token_patch.stop)
    # Entrar al cliente comparte un único event loop entre conexiones, como en uvicorn.
    client = TestClient(app)
    client.__enter__()
    test.addCleanup(client.__exit__, None, None, None)
    return client, app.state.study_graph


def _receive_until(websocket, event_type):
    events = []
    while True:
        event = websocket.receive_json()
        events.append(event)
        if event["type"] == event_type:
            return events


class WebSocketTurnLockTests(unittest.TestCase):
    def test_new_connection_supersedes_previous(self):
        client, _ = _make_client(self)
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as first:
            with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS):
                with self.assertRaises(WebSocketDisconnect) as closed:
                    first.receive_json()
        self.assertEqual(closed.exception.code, ws.WS_CLOSE_SUPERSEDED)

    def test_message_during_turn_waits_and_never_runs_in_parallel(self):
        client, graph = _make_client(self, delay=0.5)
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as first:
            first.send_json({"content": "a"})
            time.sleep(0.1)
            with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as second:
                second.send_json({"content": "b"})
                events = _receive_until(second, "agent_response")

        statuses = [event["content"] for event in events if event["type"] == "status"]
        self.assertIn("waiting_previous_turn", statuses)
        self.assertIn("previous_turn_finished", statuses)
        self.assertEqual(events[-1]["content"], "respuesta:b")
        self.assertEqual(graph.calls, ["a", "b"])
        self.assertEqual(graph.max_active, 1)

    def test_is_turn_active_follows_the_turn(self):
        client, _ = _make_client(self, delay=0.3)
        registry = client.app.state.session_registry
        self.assertFalse(registry.is_turn_active("thread"))
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
            websocket.send_json({"content": "a"})
            time.sleep(0.1)
            self.assertTrue(registry.is_turn_active("thread"))
            _receive_until(websocket, "agent_response")
            time.sleep(0.1)
            self.assertFalse(registry.is_turn_active("thread"))

    def test_connection_does_not_hold_lock_between_turns(self):
        client, _ = _make_client(self)
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as first:
            first.send_json({"content": "a"})
            _receive_until(first, "agent_response")
            with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as second:
                second.send_json({"content": "b"})
                events = _receive_until(second, "agent_response")
        self.assertEqual([event["type"] for event in events], ["agent_response"])

    def test_wait_timeout_returns_retryable_turn_in_progress(self):
        client, _ = _make_client(self, delay=1.0)
        with patch.object(ws, "TURN_WAIT_TIMEOUT_SECONDS", 0.1):
            with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as first:
                first.send_json({"content": "a"})
                time.sleep(0.1)
                with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as second:
                    second.send_json({"content": "b"})
                    events = _receive_until(second, "error")
        self.assertEqual(events[-1]["code"], "turn_in_progress")
        self.assertTrue(events[-1]["retryable"])

    def test_heartbeat_is_sent_while_turn_runs(self):
        client, _ = _make_client(self, delay=0.3)
        with patch.object(ws, "HEARTBEAT_INTERVAL_SECONDS", 0.05):
            with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
                websocket.send_json({"content": "a"})
                events = _receive_until(websocket, "agent_response")
        self.assertIn({"type": "status", "content": "working"}, events)

    def test_context_interrupt_asks_user(self):
        client, graph = _make_client(self, context_ready=False)
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
            websocket.send_json({"content": "quiero estudiar"})
            event = websocket.receive_json()
        self.assertEqual(event, {"type": "human_input_request", "content": "¿Qué nivel tienes?"})
        self.assertEqual(graph.calls, [])

    def test_rejects_unknown_origin(self):
        client, _ = _make_client(self)
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/ws/thread", headers={"origin": "https://evil.example"}, subprotocols=PROTOCOLS) as websocket:
                websocket.receive_json()


class WebSocketModelTests(unittest.TestCase):
    def test_turn_receives_resolved_models(self):
        client, graph = _make_client(self)
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
            websocket.send_json({"content": "a"})
            _receive_until(websocket, "agent_response")
        self.assertEqual(graph.configs[0]["configurable"]["models"], {"supervisor": {"model": "acme/modelo"}})


class WebSocketProgressTests(unittest.TestCase):
    UPDATE = {"topic_number": 1, "topic": "Definición", "status": "dominado", "evidence": "Bien."}

    def test_turn_receives_saved_progress(self):
        client, graph = _make_client(self)
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
            websocket.send_json({"content": "a"})
            _receive_until(websocket, "agent_response")
        self.assertEqual(graph.inputs[0]["progress"], [{"topic_number": 1, "chat_id": "thread"}])

    def test_progress_is_saved_before_the_response(self):
        client, graph = _make_client(self)
        graph.progress_updates = [self.UPDATE]
        repository = client.app.state.progress_repository
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
            websocket.send_json({"content": "a"})
            _receive_until(websocket, "agent_response")
            self.assertEqual(repository.saved[0][:2], ("thread", [self.UPDATE]))

    def test_save_failure_keeps_response(self):
        client, graph = _make_client(self)
        graph.progress_updates = [self.UPDATE]
        client.app.state.progress_repository.broken = True
        with self.assertLogs("api.ws", level="ERROR"):
            with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
                websocket.send_json({"content": "a"})
                events = _receive_until(websocket, "agent_response")
        self.assertEqual(events[-1]["content"], "respuesta:a")

    def test_context_turn_does_not_save_progress(self):
        client, _ = _make_client(self, context_ready=False)
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
            websocket.send_json({"content": "quiero estudiar"})
            websocket.receive_json()
        self.assertEqual(client.app.state.progress_repository.saved, [])


class _ClosedSocket:
    """Socket cuyo cliente se fue: cualquier envío falla."""

    def __init__(self, state):
        self.app = SimpleNamespace(state=state)

    async def send_json(self, payload):
        raise RuntimeError("socket cerrado")


class ClosedSocketProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_progress_is_saved_if_socket_closes_mid_turn(self):
        update = {"topic_number": 1, "topic": "Definición", "status": "dominado", "evidence": "Bien."}
        graph = _FakeStudyGraph(0.0, True)
        graph.progress_updates = [update]
        repository = _FakeProgressRepository()
        socket = _ClosedSocket(SimpleNamespace(
            study_graph=graph,
            document_repository=_FakeRepository(),
            progress_repository=repository,
            model_resolver=_FakeResolver(),
            usage_repository=None,
        ))
        sender = ws.SafeSender(socket)

        await ws._process_message(socket, sender, "thread", "a")

        self.assertTrue(sender.closed)
        self.assertEqual(repository.saved[0][:2], ("thread", [update]))


class WebSocketSecurityTests(unittest.TestCase):
    def test_rejects_missing_or_wrong_token(self):
        client, _ = _make_client(self)
        for protocols in ([], ["study.v1"], ["study.v1", "token.otro"]):
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=protocols) as websocket:
                    websocket.receive_json()

    def test_rejects_null_origin(self):
        client, _ = _make_client(self)
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/ws/thread", headers={"origin": "null"}, subprotocols=PROTOCOLS) as websocket:
                websocket.receive_json()

    def test_accepts_app_subprotocol(self):
        client, _ = _make_client(self)
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
            self.assertEqual(websocket.accepted_subprotocol, "study.v1")

    def test_message_too_long_keeps_connection(self):
        client, graph = _make_client(self)
        with patch.object(ws, "WS_MAX_MESSAGE_CHARS", 5):
            with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
                websocket.send_json({"content": "demasiado largo"})
                self.assertEqual(websocket.receive_json()["code"], "message_too_long")
                websocket.send_json({"content": "ok"})
                self.assertEqual(websocket.receive_json()["content"], "respuesta:ok")
        self.assertEqual(graph.calls, ["ok"])

    def test_rate_limit_per_session(self):
        client, graph = _make_client(self)
        client.app.state.session_registry.rate_limiter.max_turns = 2
        with client.websocket_connect("/ws/thread", headers=dict(ORIGIN), subprotocols=PROTOCOLS) as websocket:
            for content in ("a", "b", "c"):
                websocket.send_json({"content": content})
            events = [websocket.receive_json() for _ in range(3)]
        self.assertEqual(events[-1]["code"], "rate_limited")
        self.assertEqual(graph.calls, ["a", "b"])


class DocumentsContextTests(unittest.TestCase):
    def test_formats_ready_documents_without_llm(self):
        context = format_documents_context([
            {
                "document_type": "study_map",
                "original_filename": "guia.pdf",
                "summary": {"title": "Guía", "summary": "Derivadas.", "topics": ["Regla de la cadena"]},
            },
            {
                "document_type": "rag",
                "original_filename": "libro.pdf",
                "summary": {"title": "Libro", "summary": "Cálculo."},
            },
        ])
        self.assertIn("Mapa de estudio: Guía", context)
        self.assertIn("- Regla de la cadena", context)
        self.assertNotIn("Fórmulas", context)
        self.assertIn("Documento consultable (rag): Libro", context)


if __name__ == "__main__":
    unittest.main()
