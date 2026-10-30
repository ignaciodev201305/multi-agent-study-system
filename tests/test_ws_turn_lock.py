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
from api.ws import SessionRegistry, format_documents_context

ORIGIN = {"origin": "http://127.0.0.1:5173"}


class _FakeContextGraph:
    def __init__(self, ready=True):
        self.ready = ready

    async def aget_state(self, config):
        values = {"multi_agent_system": "sistema"} if self.ready else {}
        return SimpleNamespace(values=values, interrupts=())

    async def astream(self, graph_input, config, stream_mode):
        yield "values", {"__interrupt__": [SimpleNamespace(value="¿Qué nivel tienes?")]}


class _FakeSessionGraph:
    """Simula un turno lento y registra cuántos turnos corren a la vez."""

    def __init__(self, delay):
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self.calls = []

    async def astream(self, graph_input, config, stream_mode):
        content = graph_input["messages"][-1].content
        self.calls.append(content)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        yield "values", {"messages": [AIMessage(content=f"respuesta:{content}")]}


class _FakeRepository:
    async def list_ready_with_summaries(self, chat_id):
        return []


def _make_client(test, *, delay=0.0, context_ready=True):
    app = FastAPI()
    app.include_router(ws.router)
    app.state.session_registry = SessionRegistry()
    app.state.graph_context_study = _FakeContextGraph(context_ready)
    app.state.graph_session_study = _FakeSessionGraph(delay)
    app.state.document_repository = _FakeRepository()
    # Entrar al cliente comparte un único event loop entre conexiones, como en uvicorn.
    client = TestClient(app)
    client.__enter__()
    test.addCleanup(client.__exit__, None, None, None)
    return client, app.state.graph_session_study


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
        with client.websocket_connect("/ws/thread", headers=ORIGIN) as first:
            with client.websocket_connect("/ws/thread", headers=ORIGIN):
                with self.assertRaises(WebSocketDisconnect) as closed:
                    first.receive_json()
        self.assertEqual(closed.exception.code, ws.WS_CLOSE_SUPERSEDED)

    def test_message_during_turn_waits_and_never_runs_in_parallel(self):
        client, graph = _make_client(self, delay=0.5)
        with client.websocket_connect("/ws/thread", headers=ORIGIN) as first:
            first.send_json({"content": "a"})
            time.sleep(0.1)
            with client.websocket_connect("/ws/thread", headers=ORIGIN) as second:
                second.send_json({"content": "b"})
                events = _receive_until(second, "agent_response")

        statuses = [event["content"] for event in events if event["type"] == "status"]
        self.assertIn("waiting_previous_turn", statuses)
        self.assertIn("previous_turn_finished", statuses)
        self.assertEqual(events[-1]["content"], "respuesta:b")
        self.assertEqual(graph.calls, ["a", "b"])
        self.assertEqual(graph.max_active, 1)

    def test_connection_does_not_hold_lock_between_turns(self):
        client, _ = _make_client(self)
        with client.websocket_connect("/ws/thread", headers=ORIGIN) as first:
            first.send_json({"content": "a"})
            _receive_until(first, "agent_response")
            with client.websocket_connect("/ws/thread", headers=ORIGIN) as second:
                second.send_json({"content": "b"})
                events = _receive_until(second, "agent_response")
        self.assertEqual([event["type"] for event in events], ["agent_response"])

    def test_wait_timeout_returns_retryable_turn_in_progress(self):
        client, _ = _make_client(self, delay=1.0)
        with patch.object(ws, "TURN_WAIT_TIMEOUT_SECONDS", 0.1):
            with client.websocket_connect("/ws/thread", headers=ORIGIN) as first:
                first.send_json({"content": "a"})
                time.sleep(0.1)
                with client.websocket_connect("/ws/thread", headers=ORIGIN) as second:
                    second.send_json({"content": "b"})
                    events = _receive_until(second, "error")
        self.assertEqual(events[-1]["code"], "turn_in_progress")
        self.assertTrue(events[-1]["retryable"])

    def test_heartbeat_is_sent_while_turn_runs(self):
        client, _ = _make_client(self, delay=0.3)
        with patch.object(ws, "HEARTBEAT_INTERVAL_SECONDS", 0.05):
            with client.websocket_connect("/ws/thread", headers=ORIGIN) as websocket:
                websocket.send_json({"content": "a"})
                events = _receive_until(websocket, "agent_response")
        self.assertIn({"type": "status", "content": "working"}, events)

    def test_context_interrupt_asks_user(self):
        client, graph = _make_client(self, context_ready=False)
        with client.websocket_connect("/ws/thread", headers=ORIGIN) as websocket:
            websocket.send_json({"content": "quiero estudiar"})
            event = websocket.receive_json()
        self.assertEqual(event, {"type": "human_input_request", "content": "¿Qué nivel tienes?"})
        self.assertEqual(graph.calls, [])

    def test_rejects_unknown_origin(self):
        client, _ = _make_client(self)
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("/ws/thread", headers={"origin": "https://evil.example"}) as websocket:
                websocket.receive_json()


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
        self.assertIn("Documento consultable (rag): Libro", context)


if __name__ == "__main__":
    unittest.main()
