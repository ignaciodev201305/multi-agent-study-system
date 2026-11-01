"""Pruebas del catálogo de modelos con HTTP simulado y de su endpoint."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
from core import model_catalog
from core.model_catalog import (
    CatalogUnavailable,
    ModelCatalog,
    filter_models,
    model_fits_role,
    normalize_model,
)

TOOL_MODEL = {
    "id": "anthropic/claude-haiku-5.5",
    "name": "Anthropic: Claude Haiku 5.5",
    "context_length": 1000000,
    "architecture": {"output_modalities": ["text"]},
    "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
    "supported_parameters": ["reasoning", "tools"],
}
TEXT_MODEL = {
    "id": "acme/solo-texto",
    "name": "Acme Texto",
    "context_length": 8000,
    "architecture": {"output_modalities": ["text"]},
    "pricing": {"prompt": "0", "completion": "0"},
    "supported_parameters": ["temperature"],
}
IMAGE_MODEL = {
    "id": "acme/imagenes",
    "name": "Acme Imágenes",
    "architecture": {"output_modalities": ["image"]},
    "pricing": {"prompt": "-1", "completion": "-1"},
    "supported_parameters": ["tools"],
}


def _catalog(handler):
    return ModelCatalog(transport=httpx.MockTransport(handler))


def _response(*models):
    return httpx.Response(200, json={"data": list(models)})


class NormalizeTests(unittest.TestCase):
    def test_keeps_only_useful_fields(self):
        self.assertEqual(normalize_model(TOOL_MODEL), {
            "id": "anthropic/claude-haiku-5.5",
            "name": "Anthropic: Claude Haiku 5.5",
            "context_length": 1000000,
            "prompt_price": 0.1,
            "completion_price": 0.5,
            "supports_tools": True,
            "supports_reasoning": True,
            "supports_structured_output": False,
            "supports_temperature": False,
            "supports_text": True,
        })

    def test_router_prices_are_unknown(self):
        model = normalize_model(IMAGE_MODEL)
        self.assertIsNone(model["prompt_price"])
        self.assertIsNone(model["completion_price"])

    def test_tolerates_missing_fields(self):
        model = normalize_model({"id": "acme/minimo"})
        self.assertEqual(model["name"], "acme/minimo")
        self.assertIsNone(model["prompt_price"])
        self.assertTrue(model["supports_text"])
        self.assertFalse(model["supports_tools"])

    def test_rejects_entries_without_id(self):
        self.assertIsNone(normalize_model({"name": "sin id"}))

    def test_excludes_batch_variants(self):
        self.assertIsNone(normalize_model({**TOOL_MODEL, "id": "anthropic/claude-haiku-5.5:batch"}))


class RoleFilterTests(unittest.TestCase):
    def setUp(self):
        self.tools = normalize_model(TOOL_MODEL)
        self.text = normalize_model(TEXT_MODEL)
        self.image = normalize_model(IMAGE_MODEL)

    def test_roles_with_tools_reject_text_only_models(self):
        for role in ("supervisor", "subagent_light", "subagent_heavy", "context_classifier"):
            self.assertTrue(model_fits_role(self.tools, role), role)
            self.assertFalse(model_fits_role(self.text, role), role)

    def test_text_roles_accept_any_text_model(self):
        for role in ("factory", "summarizer"):
            self.assertTrue(model_fits_role(self.tools, role), role)
            self.assertTrue(model_fits_role(self.text, role), role)

    def test_models_without_text_output_never_fit(self):
        for role in ("supervisor", "factory", "summarizer"):
            self.assertFalse(model_fits_role(self.image, role), role)

    def test_filter_combines_role_and_query(self):
        models = [self.tools, self.text, self.image]
        self.assertEqual(filter_models(models), models)
        self.assertEqual(filter_models(models, "summarizer"), [self.tools, self.text])
        self.assertEqual(filter_models(models, "supervisor"), [self.tools])
        self.assertEqual(filter_models(models, "summarizer", " HAIKU "), [self.tools])
        self.assertEqual(filter_models(models, None, "acme texto"), [self.text])


class CatalogCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = [100.0]
        clock = SimpleNamespace(monotonic=lambda: self.clock[0])
        clock_patch = patch.object(model_catalog, "time", clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    async def test_sends_key_and_uses_cache_within_ttl(self):
        requests = []

        def handler(request):
            requests.append(request)
            return _response(TOOL_MODEL)

        catalog = _catalog(handler)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "clave-de-prueba"}):
            first = await catalog.list_models()
            self.clock[0] += model_catalog.CATALOG_TTL_SECONDS - 1
            second = await catalog.list_models()

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].headers["authorization"], "Bearer clave-de-prueba")
        self.assertEqual(str(requests[0].url), f"{model_catalog.OPENAI_API_BASE}/models")
        self.assertEqual(first, second)

    async def test_refreshes_after_ttl(self):
        responses = [_response(TOOL_MODEL), _response(TEXT_MODEL)]
        catalog = _catalog(lambda request: responses.pop(0))

        await catalog.list_models()
        self.clock[0] += model_catalog.CATALOG_TTL_SECONDS + 1
        models = await catalog.list_models()

        self.assertEqual([model["id"] for model in models], ["acme/solo-texto"])

    async def test_failure_keeps_last_copy_and_retries_soon(self):
        calls = []

        def handler(request):
            calls.append(request)
            return _response(TOOL_MODEL) if len(calls) == 1 else httpx.Response(500)

        catalog = _catalog(handler)
        first = await catalog.list_models()
        self.clock[0] += model_catalog.CATALOG_TTL_SECONDS + 1

        with self.assertLogs("core.model_catalog", level="WARNING"):
            self.assertEqual(await catalog.list_models(), first)
        self.assertEqual(await catalog.list_models(), first)
        self.assertEqual(len(calls), 2)

        self.clock[0] += model_catalog.CATALOG_RETRY_SECONDS + 1
        with self.assertLogs("core.model_catalog", level="WARNING"):
            await catalog.list_models()
        self.assertEqual(len(calls), 3)

    async def test_failure_without_copy_raises(self):
        def handler(request):
            raise httpx.ConnectError("sin red", request=request)

        with self.assertLogs("core.model_catalog", level="WARNING"):
            with self.assertRaises(CatalogUnavailable):
                await _catalog(handler).list_models()

    async def test_malformed_or_empty_responses_count_as_failure(self):
        for response in (
            httpx.Response(200, json={"otra": []}),
            httpx.Response(200, text="no es json"),
            httpx.Response(200, json={"data": []}),
            httpx.Response(200, json={"data": [{"name": "sin id"}]}),
        ):
            with self.assertLogs("core.model_catalog", level="WARNING"):
                with self.assertRaises(CatalogUnavailable):
                    await _catalog(lambda request, value=response: value).list_models()

    async def test_log_does_not_expose_key(self):
        def handler(request):
            return httpx.Response(401)

        with patch.dict("os.environ", {"OPENAI_API_KEY": "clave-secreta"}):
            with self.assertLogs("core.model_catalog", level="WARNING") as logs:
                with self.assertRaises(CatalogUnavailable):
                    await _catalog(handler).list_models()
        self.assertNotIn("clave-secreta", "\n".join(logs.output))


class ModelsEndpointTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        models = [normalize_model(TOOL_MODEL), normalize_model(TEXT_MODEL)]
        app.state.model_catalog = SimpleNamespace(list_models=AsyncMock(return_value=models))
        self.client = TestClient(app)

    def ids(self, **params):
        response = self.client.get("/models", params=params)
        self.assertEqual(response.status_code, 200)
        return [model["id"] for model in response.json()["models"]]

    def test_lists_every_model_by_default(self):
        self.assertEqual(self.ids(), ["anthropic/claude-haiku-5.5", "acme/solo-texto"])

    def test_filters_by_role_and_query(self):
        self.assertEqual(self.ids(role="supervisor"), ["anthropic/claude-haiku-5.5"])
        self.assertEqual(self.ids(role="factory", q="acme"), ["acme/solo-texto"])

    def test_unknown_role_is_rejected(self):
        self.assertEqual(self.client.get("/models", params={"role": "otro"}).status_code, 422)

    def test_long_query_is_rejected(self):
        self.assertEqual(self.client.get("/models", params={"q": "x" * 101}).status_code, 422)

    def test_catalog_failure_is_503(self):
        self.client.app.state.model_catalog.list_models.side_effect = CatalogUnavailable
        self.assertEqual(self.client.get("/models").status_code, 503)


if __name__ == "__main__":
    unittest.main()
