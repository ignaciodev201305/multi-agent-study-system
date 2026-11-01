"""Pruebas de la salida estructurada con fallback al parser, con un servidor simulado."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import TypedDict
from unittest.mock import AsyncMock, patch

import httpx
import openai
from langgraph.graph import START, StateGraph
from pydantic import BaseModel

from core.chain import ChainBuilder
from core.graphs.context import ContextStudyGraph
from core.llm import OpenRouterChat
from core.prompts.context import STUDY_PLAN_JSON_RULES, STUDY_PLAN_PROMPT
from core.schemas import ContextClassification, StudyPlan
from core.usage import UsageTracker

USAGE = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "cost": 0.01}
VALID = '{"subject": "derivadas"}'
SINGLE_QUOTES = "{\"subject\": 'derivadas'}"
WRONG_SHAPE = '{"tema": "derivadas"}'


PLAN = StudyPlan.model_validate({
    "subject": "derivadas",
    "syllabus": ["Definición"],
    "archetypes": [{"archetype": "explicador", "focus": "Derivadas."}],
    "supervisor_focus": "Guiar con ejercicios.",
})


class _Plan(BaseModel):
    subject: str


class _GraphState(TypedDict, total=False):
    result: _Plan


def _completion(content):
    return httpx.Response(200, json={
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": "acme/modelo",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": USAGE,
    })


def _stream(content):
    base = {"id": "gen-1", "object": "chat.completion.chunk", "created": 1, "model": "acme/modelo"}
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "stop"}]},
        {**base, "choices": [], "usage": USAGE},
    ]
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


class _Server:
    """Responde con un guion y guarda cada pedido."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, request):
        self.requests.append(json.loads(request.content))
        return self.script.pop(0)

    def model(self):
        return OpenRouterChat(
            model="acme/modelo",
            api_key="clave-de-prueba",
            base_url="https://openrouter.test/api/v1",
            max_retries=0,
            http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(self)),
            tags=["role:factory", "model:acme/modelo"],
        )


def _chain(server, native=True):
    return ChainBuilder().build_structured_chain(
        server.model(),
        "principal {context}",
        _Plan,
        "fallback {context} {format_instructions}",
        native=native,
    )


class StructuredChainTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, server, native=True):
        tracker = UsageTracker()
        result = await _chain(server, native).ainvoke({"context": "x"}, config={"callbacks": [tracker]})
        return result, tracker

    async def test_structured_output_is_used_first(self):
        server = _Server(_completion(VALID))
        result, tracker = await self.invoke(server)
        self.assertEqual(result, _Plan(subject="derivadas"))
        self.assertEqual(server.requests[0]["response_format"]["type"], "json_schema")
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(tracker.records[0].cost_usd, 0.01)

    async def test_invalid_outputs_fall_back_to_parser(self):
        for content in (SINGLE_QUOTES, WRONG_SHAPE, "no es json"):
            with self.subTest(content=content):
                server = _Server(_completion(content), _completion(VALID))
                with self.assertLogs("core.chain", level="WARNING"):
                    result, _ = await self.invoke(server)
                self.assertEqual(result.subject, "derivadas")
                self.assertNotIn("response_format", server.requests[1])

    async def test_unsupported_format_falls_back_to_parser(self):
        for status in (400, 404):
            with self.subTest(status=status):
                rejected = httpx.Response(status, json={"error": {"message": "sin soporte"}})
                server = _Server(rejected, _completion(VALID))
                with self.assertLogs("core.chain", level="WARNING"):
                    result, _ = await self.invoke(server)
                self.assertEqual(result.subject, "derivadas")

    async def test_unresponsive_provider_falls_back_to_parser(self):
        async def respond(request):
            if "response_format" in json.loads(request.content):
                await asyncio.sleep(5)
            return _completion(VALID)

        model = OpenRouterChat(
            model="acme/modelo",
            api_key="clave-de-prueba",
            base_url="https://openrouter.test/api/v1",
            http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        with patch("core.chain.STRUCTURED_TIMEOUT_SECONDS", 0.05), self.assertLogs("core.chain", level="WARNING"):
            chain = ChainBuilder().build_structured_chain(
                model, "principal {context}", _Plan, "fallback {context} {format_instructions}"
            )
            result = await chain.ainvoke({"context": "x"})
        self.assertEqual(result.subject, "derivadas")

    async def test_unresponsive_fallback_reports_timeout(self):
        async def respond(request):
            await asyncio.sleep(5)
            return _completion(VALID)

        model = OpenRouterChat(
            model="acme/modelo",
            api_key="clave-de-prueba",
            base_url="https://openrouter.test/api/v1",
            http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        with (
            patch("core.chain.STRUCTURED_TIMEOUT_SECONDS", 0.05),
            patch("core.chain.PARSER_TIMEOUT_SECONDS", 0.05),
            self.assertLogs("core.chain", level="WARNING"),
            self.assertRaises(TimeoutError),
        ):
            chain = ChainBuilder().build_structured_chain(
                model, "principal {context}", _Plan, "fallback {context} {format_instructions}"
            )
            await chain.ainvoke({"context": "x"})

    async def test_both_calls_are_recorded(self):
        server = _Server(_completion(WRONG_SHAPE), _completion(VALID))
        with self.assertLogs("core.chain", level="WARNING"):
            _, tracker = await self.invoke(server)
        self.assertEqual(len(tracker.records), 2)

    async def test_quota_and_auth_errors_do_not_fall_back(self):
        for status, error in ((429, openai.RateLimitError), (401, openai.AuthenticationError)):
            with self.subTest(status=status):
                server = _Server(httpx.Response(status, json={"error": {"message": "no"}}))
                with self.assertRaises(error):
                    await self.invoke(server)
                self.assertEqual(len(server.requests), 1)

    async def test_fallback_failure_is_reported(self):
        server = _Server(_completion(WRONG_SHAPE), _completion(SINGLE_QUOTES))
        with self.assertLogs("core.chain", level="WARNING"), self.assertRaises(ValueError):
            await self.invoke(server)
        self.assertEqual(len(server.requests), 2)

    async def test_model_without_support_goes_straight_to_parser(self):
        server = _Server(_completion(VALID))
        result, _ = await self.invoke(server, native=False)
        self.assertEqual(result.subject, "derivadas")
        self.assertNotIn("response_format", server.requests[0])
        self.assertIn("subject", server.requests[0]["messages"][0]["content"])

    async def stream_in_graph(self, server):
        chain = _chain(server)

        async def node(state):
            return {"result": await chain.ainvoke({"context": "x"})}

        builder = StateGraph(_GraphState)
        builder.add_node("plan", node)
        builder.add_edge(START, "plan")
        tracker = UsageTracker()
        result = None
        async for mode, chunk in builder.compile().astream(
            {}, {"callbacks": [tracker]}, stream_mode=["messages", "values"]
        ):
            if mode == "values":
                result = chunk.get("result")
        return result, tracker

    async def test_graph_streaming_keeps_tokens_and_cost(self):
        server = _Server(_completion(VALID))
        result, tracker = await self.stream_in_graph(server)
        self.assertEqual(result, _Plan(subject="derivadas"))
        self.assertFalse(server.requests[0]["stream"])
        self.assertEqual(len(tracker.records), 1)
        self.assertEqual(tracker.records[0].input_tokens, 100)
        self.assertEqual(tracker.records[0].cost_usd, 0.01)

    async def test_graph_streaming_fallback_records_both_calls(self):
        for content in ("", WRONG_SHAPE):
            with self.subTest(content=content):
                server = _Server(_completion(content), _stream(VALID))
                with self.assertLogs("core.chain", level="WARNING"):
                    result, tracker = await self.stream_in_graph(server)
                self.assertEqual(result.subject, "derivadas")
                self.assertEqual([record.cost_usd for record in tracker.records], [0.01, 0.01])
                self.assertEqual([record.input_tokens for record in tracker.records], [100, 100])


class PlanPromptTests(unittest.TestCase):
    def test_prompts_do_not_ask_for_single_quoted_json(self):
        self.assertNotIn("nunca dobles", STUDY_PLAN_PROMPT + STUDY_PLAN_JSON_RULES)
        self.assertNotIn("format_instructions", STUDY_PLAN_PROMPT)
        self.assertIn("comillas dobles", STUDY_PLAN_JSON_RULES)


class ContextChainSelectionTests(unittest.IsolatedAsyncioTestCase):
    async def native_for(self, capability):
        with patch("core.graphs.context.build_model"), patch("core.graphs.context.ToolsBuilder"):
            graph = ContextStudyGraph()
        calls = []

        def build(*_, native):
            calls.append(native)
            return SimpleNamespace(ainvoke=AsyncMock(return_value=PLAN))

        graph.chain_builder = SimpleNamespace(build_structured_chain=build)
        context = ContextClassification(approved_context=False, title_session="Derivadas")
        models = {"factory": {"model": "acme/modelo", "supports_structured_output": capability}}
        with patch("core.llm.build_model"):
            await graph.build_subgraph().ainvoke(
                {"messages": [], "classified_context": context, "clarification_round": 3},
                {"configurable": {"models": models}},
            )
        return calls[0]

    async def test_factory_uses_catalog_capability(self):
        self.assertFalse(await self.native_for(False))
        self.assertTrue(await self.native_for(True))
        self.assertTrue(await self.native_for(None))


if __name__ == "__main__":
    unittest.main()
