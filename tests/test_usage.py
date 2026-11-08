"""Pruebas del registro de tokens y costo: captura, grafos, SQLite, API y WebSocket."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain.messages import AIMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.checkpoint.memory import InMemorySaver

from api import routes, ws
from core.graphs.study import StudyGraph
from core.llm import OpenRouterChat, build_model
from core.model_catalog import CatalogUnavailable
from core.schemas import ContextClassification, StudyPlan
from core.streaming import execute_astream_graph
from core.usage import UsageRecord, UsageRepository, UsageTracker, estimate_cost, session_usage
from documents.worker import process_document_job

USAGE = {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120,
    "prompt_tokens_details": {"cached_tokens": 30},
    "completion_tokens_details": {"reasoning_tokens": 5},
}
PLAN = StudyPlan.model_validate({
    "subject": "derivadas",
    "syllabus": ["Definición"],
    "archetypes": [{"archetype": "explicador", "focus": "Derivadas."}],
    "supervisor_focus": "Guiar con ejercicios.",
})
ENTRY = {"id": "acme/modelo", "prompt_price": 2.0, "completion_price": 10.0, "context_length": 1000}


class _ToolModel(GenericFakeChatModel):
    """Modelo simulado que acepta bind_tools y responde con mensajes guionados."""
    # El modelo simulado no puede hacer stream de un mensaje vacío con tool_calls.
    disable_streaming: bool = True

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        return self | JsonOutputParser()


def _fake_model(role, *messages):
    return _ToolModel(messages=iter(messages), tags=[f"role:{role}", f"model:acme/{role}"])


def _message(content="", input_tokens=10, output_tokens=2, tool_calls=None):
    return AIMessage(
        content=content,
        tool_calls=tool_calls or [],
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


def _result(message):
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def _catalog(*entries):
    return SimpleNamespace(list_models=AsyncMock(return_value=list(entries)))


def _openrouter(body, content_type):
    """Modelo real de la aplicación contra un servidor simulado."""
    def respond(request):
        return httpx.Response(200, headers={"content-type": content_type}, content=body)

    return OpenRouterChat(
        model="acme/modelo",
        api_key="clave-de-prueba",
        base_url="https://openrouter.test/api/v1",
        http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        tags=["role:supervisor", "model:acme/modelo"],
    )


def _stream_body(usage):
    base = {"id": "gen-1", "object": "chat.completion.chunk", "created": 1, "model": "acme/modelo-20260101"}
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ho"}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {"content": "la"}, "finish_reason": "stop"}]},
        {**base, "choices": [], "usage": usage},
    ]
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return "".join(lines + ["data: [DONE]\n\n"]).encode()


def _complete_body(usage):
    return json.dumps({
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": "acme/modelo-20260101",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "hola"}, "finish_reason": "stop"}],
        "usage": usage,
    }).encode()


class OpenRouterCostTests(unittest.IsolatedAsyncioTestCase):
    async def record(self, usage, *, streaming, catalog=None):
        body, content_type = (
            (_stream_body(usage), "text/event-stream") if streaming
            else (_complete_body(usage), "application/json")
        )
        tracker = UsageTracker(catalog=catalog)
        model = _openrouter(body, content_type)
        config = {"callbacks": [tracker]}
        if streaming:
            async for _ in model.astream("hola", config=config):
                pass
        else:
            await model.ainvoke("hola", config=config)
        self.assertEqual(len(tracker.records), 1)
        return tracker.records[0]

    async def test_cost_survives_with_and_without_streaming(self):
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                record = await self.record({**USAGE, "cost": 0.0123}, streaming=streaming)
                self.assertEqual(record.cost_usd, 0.0123)
                self.assertFalse(record.cost_estimated)
                self.assertEqual((record.input_tokens, record.output_tokens), (100, 20))
                self.assertEqual((record.cached_tokens, record.reasoning_tokens), (30, 5))

    async def test_free_model_keeps_zero_cost(self):
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                record = await self.record({**USAGE, "cost": 0}, streaming=streaming)
                self.assertEqual(record.cost_usd, 0)
                self.assertFalse(record.cost_estimated)

    async def test_missing_cost_is_estimated_with_requested_model(self):
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                record = await self.record(USAGE, streaming=streaming, catalog=_catalog(ENTRY))
                self.assertEqual(record.model, "acme/modelo")
                self.assertAlmostEqual(record.cost_usd, 0.0004)
                self.assertTrue(record.cost_estimated)

    async def test_invalid_cost_does_not_break_the_call(self):
        record = await self.record({**USAGE, "cost": "gratis"}, streaming=True)
        self.assertIsNone(record.cost_usd)

    def test_build_model_uses_cost_aware_class(self):
        self.assertIsInstance(build_model("supervisor"), OpenRouterChat)


class UsageTrackerTests(unittest.IsolatedAsyncioTestCase):
    TAGS = ["seq:step:1", "role:summarizer", "model:acme/modelo"]

    async def test_extracts_role_tokens_and_cache(self):
        tracker = UsageTracker(source="document")
        message = _message(input_tokens=50, output_tokens=7)
        message.usage_metadata["input_token_details"] = {"cache_read": 11}
        message.usage_metadata["output_token_details"] = {"reasoning": 3}

        await tracker.on_llm_end(_result(message), run_id=uuid4(), tags=self.TAGS)

        self.assertEqual(tracker.records, [UsageRecord(
            role="summarizer",
            model="acme/modelo",
            input_tokens=50,
            output_tokens=7,
            cached_tokens=11,
            reasoning_tokens=3,
            source="document",
        )])

    async def test_ignores_calls_without_usage(self):
        tracker = UsageTracker()
        with self.assertLogs("core.usage", level="WARNING"):
            await tracker.on_llm_end(_result(AIMessage(content="x")), run_id=uuid4(), tags=self.TAGS)
        self.assertEqual(tracker.records, [])

    async def test_internal_error_does_not_propagate(self):
        tracker = UsageTracker(on_record=AsyncMock(side_effect=RuntimeError("fallo")))
        with self.assertLogs("core.usage", level="ERROR"):
            await tracker.on_llm_end(_result(_message()), run_id=uuid4(), tags=self.TAGS)

    async def test_unavailable_catalog_leaves_cost_unknown(self):
        catalog = SimpleNamespace(list_models=AsyncMock(side_effect=CatalogUnavailable))
        tracker = UsageTracker(catalog=catalog)
        await tracker.on_llm_end(_result(_message()), run_id=uuid4(), tags=self.TAGS)
        self.assertIsNone(tracker.records[0].cost_usd)
        self.assertFalse(tracker.records[0].cost_estimated)


class EstimateCostTests(unittest.TestCase):
    def test_cases(self):
        record = UsageRecord("supervisor", "m", input_tokens=1_000_000, output_tokens=500_000)
        cases = [
            ({"prompt_price": 1.0, "completion_price": 4.0}, 3.0),
            ({"prompt_price": 0.0, "completion_price": 0.0}, 0.0),
            ({"prompt_price": None, "completion_price": 4.0}, None),
            ({"prompt_price": 1.0, "completion_price": None}, None),
            ({}, None),
            (None, None),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                self.assertEqual(estimate_cost(record, entry), expected)


class RepositoryCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.directory.cleanup)
        self.repository = await UsageRepository.open(Path(self.directory.name) / "app.db")
        self.addAsyncCleanup(self.repository.close)


class UsageRepositoryTests(RepositoryCase):
    async def test_empty_session(self):
        summary = await self.repository.summary("c")
        self.assertEqual(summary["total_tokens"], 0)
        self.assertIsNone(summary["cost_usd"])
        self.assertIsNone(summary["last_turn_cost_usd"])
        self.assertEqual(summary["context_used"], 0)
        self.assertEqual(summary["by_model"], [])

    async def test_totals_by_role_and_last_turn(self):
        await self.repository.add_records("c", "t1", [
            UsageRecord("supervisor", "a", 100, 10, cached_tokens=5, cost_usd=0.1),
            UsageRecord("subagent_light", "b", 900, 90, reasoning_tokens=4, cost_usd=0.2),
        ])
        await self.repository.add_records("c", "t2", [
            UsageRecord("supervisor", "a", 200, 20, cost_usd=0.3),
            UsageRecord("subagent_light", "b", 5000, 50, cost_usd=0.4),
        ])
        await self.repository.add_records("c", "doc", [
            UsageRecord("summarizer", "s", 1, 1, cost_usd=1.0, source="document"),
        ])
        await self.repository.add_records("otra", "t", [UsageRecord("supervisor", "a", 7, 7, cost_usd=9.0)])

        summary = await self.repository.summary("c")

        self.assertEqual(summary["input_tokens"], 6201)
        self.assertEqual(summary["output_tokens"], 171)
        self.assertEqual(summary["total_tokens"], 6372)
        self.assertEqual((summary["cached_tokens"], summary["reasoning_tokens"]), (5, 4))
        self.assertAlmostEqual(summary["cost_usd"], 2.0)
        self.assertAlmostEqual(summary["last_turn_cost_usd"], 0.7)
        # Ni el subagente ni el documento cuentan para el contexto.
        self.assertEqual(summary["context_used"], 220)
        self.assertEqual(summary["context_model"], "a")
        self.assertEqual(
            [(row["role"], row["calls"]) for row in summary["by_model"]],
            [("summarizer", 1), ("subagent_light", 2), ("supervisor", 2)],
        )

    async def test_estimated_and_unknown_costs(self):
        await self.repository.add_records("c", "t", [
            UsageRecord("supervisor", "a", 1, 1, cost_usd=0.5, cost_estimated=True),
            UsageRecord("factory", "b", 1, 1),
        ])
        summary = await self.repository.summary("c")
        self.assertEqual(summary["cost_usd"], 0.5)
        self.assertTrue(summary["cost_estimated"])
        self.assertTrue(summary["cost_unknown"])

    async def test_unknown_cost_is_not_zero(self):
        await self.repository.add_records("c", "t", [UsageRecord("factory", "b", 1, 1)])
        summary = await self.repository.summary("c")
        self.assertIsNone(summary["cost_usd"])
        self.assertIsNone(summary["last_turn_cost_usd"])
        self.assertFalse(summary["cost_estimated"])

    async def test_delete_keeps_other_sessions(self):
        await self.repository.add_records("c", "t", [UsageRecord("supervisor", "a", 1, 1)])
        await self.repository.add_records("otra", "t", [UsageRecord("supervisor", "a", 1, 1)])
        await self.repository.delete_chat("c")
        self.assertEqual((await self.repository.summary("c"))["total_tokens"], 0)
        self.assertEqual((await self.repository.summary("otra"))["total_tokens"], 2)

    async def test_context_limit_follows_last_supervisor_model(self):
        catalog = _catalog({"id": "a", "context_length": 100}, {"id": "b", "context_length": 200})
        resolver = SimpleNamespace(
            catalog=catalog,
            resolve=AsyncMock(return_value={"supervisor": {"model": "b"}}),
        )
        self.assertEqual((await session_usage(self.repository, resolver, "c"))["context_limit"], 200)
        await self.repository.add_records("c", "t", [UsageRecord("supervisor", "a", 1, 1)])
        self.assertEqual((await session_usage(self.repository, resolver, "c"))["context_limit"], 100)

    async def test_context_limit_unknown(self):
        await self.repository.add_records("c", "t", [UsageRecord("supervisor", "x", 1, 1)])
        known = SimpleNamespace(catalog=_catalog(ENTRY))
        down = SimpleNamespace(catalog=SimpleNamespace(list_models=AsyncMock(side_effect=CatalogUnavailable)))
        for resolver in (known, down):
            self.assertIsNone((await session_usage(self.repository, resolver, "c"))["context_limit"])


class _Sender:
    async def send_json(self, payload):
        pass


class GraphUsageTests(unittest.IsolatedAsyncioTestCase):
    """Los callbacks de la configuración deben llegar a cada modelo sin tocar los grafos."""

    def build_graph(self, models):
        with (
            patch("core.graphs.session.build_model"),
            patch("core.graphs.context.build_model"),
            patch("core.graphs.context.build_tools", return_value=[]),
        ):
            return StudyGraph()

    async def run_turn(self, graph, tracker, values, thread_id="t"):
        config = {"configurable": {"thread_id": thread_id}, "callbacks": [tracker]}
        await execute_astream_graph(graph, _Sender(), values, config)

    async def test_supervisor_and_subagent_are_recorded(self):
        models = {
            "supervisor": _fake_model(
                "supervisor",
                _message(input_tokens=100, output_tokens=5, tool_calls=[{
                    "name": "explicador",
                    "args": {"request": "Explica la derivada."},
                    "id": "call-1",
                }]),
                _message("Listo.", input_tokens=300, output_tokens=40),
            ),
            "subagent_light": _fake_model("subagent_light", _message("Es una tasa.", input_tokens=9000, output_tokens=900)),
        }
        study_graph = self.build_graph(models)
        with (
            patch("core.agents.builder.build_model", side_effect=lambda role, **_: models[role]),
            patch("core.agents.builder.build_tools", return_value=[]),
        ):
            graph = await study_graph.compile_graph(InMemorySaver())
            tracker = UsageTracker()
            await self.run_turn(graph, tracker, {"messages": [HumanMessage(content="hola")], "study_plan": PLAN})

        self.assertEqual(
            sorted(record.role for record in tracker.records),
            ["subagent_light", "supervisor", "supervisor"],
        )

        directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(directory.cleanup)
        repository = await UsageRepository.open(Path(directory.name) / "app.db")
        self.addAsyncCleanup(repository.close)
        await repository.add_records("c", "t", tracker.records)
        summary = await repository.summary("c")
        self.assertEqual(summary["context_used"], 340)
        self.assertEqual(summary["total_tokens"], 10345)

    async def test_context_subgraph_is_recorded(self):
        study_graph = self.build_graph({})
        classification = ContextClassification.model_validate({
            "title_session": "Derivadas",
            "session_objetive": "Aprender.",
            "session_context": "Curso.",
            "previous_level": "principiante",
            "approved_context": True,
        })
        study_graph.context.model = _fake_model("context_classifier", _message(input_tokens=70, tool_calls=[{
            "name": "ContextClassification",
            "args": classification.model_dump(),
            "id": "call-1",
        }]))
        factory = _fake_model("factory", _message(PLAN.model_dump_json(), input_tokens=80))
        study_graph.context.factory_model = factory
        graph = await study_graph.compile_graph(InMemorySaver())
        tracker = UsageTracker()

        # El clasificador arma su búsqueda web en cada ronda.
        with patch("core.graphs.context.build_tools", return_value=[]):
            await self.run_turn(graph, tracker, {"messages": [HumanMessage(content="derivadas")]})

        self.assertEqual(
            [(record.role, record.input_tokens) for record in tracker.records],
            [("context_classifier", 70), ("factory", 80)],
        )


class _SpendingGraph:
    """Simula un turno que hace dos llamadas y luego termina o falla."""

    def __init__(self, error=None):
        self.error = error

    async def aget_state(self, config):
        return SimpleNamespace(values={"study_plan": "plan"}, interrupts=())

    async def astream(self, graph_input, config, stream_mode, subgraphs):
        tracker = config["callbacks"][0]
        tags = ["role:supervisor", "model:acme/modelo"]
        await tracker.on_llm_end(_result(_message(input_tokens=100, output_tokens=10)), run_id=uuid4(), tags=tags)
        await tracker.on_llm_end(_result(_message(input_tokens=200, output_tokens=20)), run_id=uuid4(), tags=tags)
        if self.error:
            raise self.error
        yield (), "values", {"messages": [AIMessage(content="fin")], "progress_updates": []}


class _ClosedSocket:
    """Socket cuyo cliente se fue: cualquier envío falla."""

    def __init__(self, state):
        self.app = SimpleNamespace(state=state)

    async def send_json(self, payload):
        raise RuntimeError("socket cerrado")


class _OpenSocket(_ClosedSocket):
    def __init__(self, state):
        super().__init__(state)
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class WebSocketUsageTests(RepositoryCase):
    def state(self, graph):
        async def resolve(chat_id=None):
            return {"supervisor": {"model": "acme/modelo"}}

        return SimpleNamespace(
            study_graph=graph,
            document_repository=SimpleNamespace(list_ready_with_summaries=AsyncMock(return_value=[])),
            progress_repository=SimpleNamespace(
                list_for_chat=AsyncMock(return_value=[]),
                apply_updates=AsyncMock(),
            ),
            model_resolver=SimpleNamespace(resolve=resolve, catalog=_catalog(ENTRY)),
            usage_repository=self.repository,
        )

    async def test_sends_event_after_each_call(self):
        socket = _OpenSocket(self.state(_SpendingGraph()))
        await ws._process_message(socket, ws.SafeSender(socket), "thread", "a")

        events = [event for event in socket.sent if event["type"] == "usage"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[-1], {
            "type": "usage",
            "content": "",
            "context_used": 220,
            "context_limit": 1000,
            "total_tokens": 330,
            "cost_usd": 0.0009,
            "cost_estimated": True,
            "cost_unknown": False,
        })
        self.assertEqual(socket.sent[-1]["type"], "agent_response")

    async def test_failed_turn_is_saved(self):
        socket = _OpenSocket(self.state(_SpendingGraph(RuntimeError("fallo del modelo"))))
        with self.assertRaises(RuntimeError):
            await ws._process_message(socket, ws.SafeSender(socket), "thread", "a")
        self.assertEqual((await self.repository.summary("thread"))["total_tokens"], 330)

    async def test_saved_if_socket_closes_mid_turn(self):
        socket = _ClosedSocket(self.state(_SpendingGraph()))
        sender = ws.SafeSender(socket)
        await ws._process_message(socket, sender, "thread", "a")
        self.assertTrue(sender.closed)
        self.assertEqual((await self.repository.summary("thread"))["total_tokens"], 330)

    async def test_save_failure_is_retried_and_keeps_the_turn(self):
        state = self.state(_SpendingGraph())
        state.usage_repository = SimpleNamespace(
            add_records=AsyncMock(side_effect=[RuntimeError("base ocupada"), None]),
            summary=self.repository.summary,
        )
        socket = _OpenSocket(state)
        with self.assertLogs("api.ws", level="ERROR"):
            await ws._process_message(socket, ws.SafeSender(socket), "thread", "a")
        self.assertEqual(socket.sent[-1]["type"], "agent_response")
        pending = state.usage_repository.add_records.call_args.args[2]
        self.assertEqual(len(pending), 2)

    async def test_parallel_calls_are_saved_once(self):
        tracker_ready = asyncio.Event()

        class Graph(_SpendingGraph):
            async def astream(self, graph_input, config, stream_mode, subgraphs):
                tracker = config["callbacks"][0]
                tracker_ready.set()
                tags = ["role:subagent_light", "model:acme/modelo"]
                await asyncio.gather(*(
                    tracker.on_llm_end(_result(_message()), run_id=uuid4(), tags=tags)
                    for _ in range(5)
                ))
                yield (), "values", {"messages": [AIMessage(content="fin")], "progress_updates": []}

        socket = _OpenSocket(self.state(Graph()))
        await ws._process_message(socket, ws.SafeSender(socket), "thread", "a")
        self.assertTrue(tracker_ready.is_set())
        self.assertEqual((await self.repository.summary("thread"))["by_model"][0]["calls"], 5)


class DocumentUsageTests(RepositoryCase):
    DOCUMENT = {"document_id": "d", "chat_id": "c", "document_type": "rag"}

    def repositories(self, *lookups):
        return MagicMock(
            get_document=AsyncMock(side_effect=lookups),
            lock_chat=MagicMock(return_value=AsyncMock()),
            claim_queued_document=AsyncMock(return_value=self.DOCUMENT),
            mark_ready=AsyncMock(return_value=True),
            mark_failed=AsyncMock(),
        )

    def pipeline(self, error=None):
        async def process(document, pdf_path, tracker):
            tags = ["role:summarizer", "model:acme/modelo"]
            await tracker.on_llm_end(_result(_message()), run_id=uuid4(), tags=tags)
            if error:
                raise error
            return {"title": "x"}, 1

        return SimpleNamespace(usage_tracker=lambda: UsageTracker(source="document"), process=process)

    async def test_success_is_saved_as_document(self):
        documents = self.repositories(self.DOCUMENT, self.DOCUMENT)
        await process_document_job("d", documents, self.pipeline(), self.repository)
        summary = await self.repository.summary("c")
        self.assertEqual(summary["total_tokens"], 12)
        self.assertEqual(summary["by_model"][0]["role"], "summarizer")
        self.assertEqual(summary["context_used"], 0)
        self.assertIsNone(summary["last_turn_cost_usd"])

    async def test_failure_is_saved(self):
        documents = self.repositories(self.DOCUMENT, self.DOCUMENT)
        with self.assertLogs("documents.worker", level="ERROR"):
            await process_document_job("d", documents, self.pipeline(RuntimeError("fallo")), self.repository)
        documents.mark_failed.assert_awaited_once()
        self.assertEqual((await self.repository.summary("c"))["total_tokens"], 12)

    async def test_deleted_session_leaves_no_rows(self):
        documents = self.repositories(self.DOCUMENT, None, None)
        await process_document_job("d", documents, self.pipeline(), self.repository)
        self.assertEqual((await self.repository.summary("c"))["total_tokens"], 0)


class UsageRoutesTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        app.state.usage_repository = SimpleNamespace(
            summary=AsyncMock(return_value={"context_model": "acme/modelo", "context_used": 9, "total_tokens": 12}),
            delete_chat=AsyncMock(),
        )
        app.state.model_resolver = SimpleNamespace(catalog=_catalog(ENTRY))
        app.state.progress_repository = SimpleNamespace(delete_chat=AsyncMock(), list_for_chat=AsyncMock(return_value=[]))
        app.state.document_repository = SimpleNamespace(delete_chat=AsyncMock())
        app.state.model_settings = SimpleNamespace(delete_chat=AsyncMock())
        app.state.study_graph = SimpleNamespace(
            aget_state=AsyncMock(return_value=SimpleNamespace(values={}, interrupts=())),
            checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
        )
        app.state.study_graph_instance = MagicMock()
        app.state.session_registry = MagicMock()
        app.state.session_registry.is_turn_active.return_value = False
        self.client = TestClient(app)

    def test_usage_endpoint(self):
        self.assertEqual(self.client.get("/chats/c/usage").json(), {
            "chat_id": "c",
            "context_used": 9,
            "total_tokens": 12,
            "context_limit": 1000,
        })

    def test_snapshot_includes_usage(self):
        usage = self.client.get("/chats/c/messages").json()["usage"]
        self.assertEqual((usage["context_used"], usage["context_limit"]), (9, 1000))

    def test_delete_removes_usage(self):
        with patch("api.routes.SessionManager"), patch("api.routes.forget_vector_store"):
            self.assertEqual(self.client.delete("/chats/c").status_code, 200)
        self.client.app.state.usage_repository.delete_chat.assert_awaited_once_with("c")


if __name__ == "__main__":
    unittest.main()
