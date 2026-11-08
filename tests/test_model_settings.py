"""Pruebas de preferencias de modelos: prioridad, validación, API y aplicación por turno."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain.messages import AIMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.types import Command

from api import routes
from config.settings import MODEL_ROLES
from core.graphs import SessionStudyGraph, StudyGraph, open_checkpointer
from core.llm import build_model, model_for_turn
from core.model_catalog import CatalogUnavailable, normalize_model
from core.model_settings import GLOBAL_SCOPE, InvalidModel, ModelResolver, ModelSettingsRepository
from core.schemas import ContextClassification, StudyPlan
from core.streaming import execute_astream_graph

TOOLS_MODEL = "acme/con-tools"
OTHER_TOOLS_MODEL = "acme/otro-con-tools"
TEXT_MODEL = "acme/solo-texto"
CATALOG_LIMITED = "acme/pocos-niveles"


def _raw(model_id, *parameters):
    return {"id": model_id, "supported_parameters": list(parameters)}


CATALOG = [
    normalize_model({
        **_raw(TOOLS_MODEL, "tools", "temperature", "reasoning"),
        "reasoning": {"supported_efforts": ["none", "low", "high"], "default_effort": "low"},
    }),
    normalize_model(_raw(OTHER_TOOLS_MODEL, "tools")),
    normalize_model(_raw(TEXT_MODEL, "temperature")),
    normalize_model({
        **_raw(CATALOG_LIMITED, "tools", "reasoning"),
        "reasoning": {"supported_efforts": ["low"], "default_effort": "low"},
    }),
]


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "app.db"
        self.repository = await ModelSettingsRepository.open(self.database)
        self.catalog = SimpleNamespace(list_models=AsyncMock(return_value=CATALOG))
        self.resolver = ModelResolver(self.repository, self.catalog)

    async def asyncTearDown(self):
        await self.repository.close()
        self.directory.cleanup()


class RepositoryTests(_Base):
    async def test_set_replaces_and_none_removes(self):
        await self.repository.set("c", "supervisor", TOOLS_MODEL)
        await self.repository.set("c", "supervisor", OTHER_TOOLS_MODEL)
        self.assertEqual(await self.repository.get("c"), {"supervisor": OTHER_TOOLS_MODEL})
        await self.repository.set("c", "supervisor", None)
        self.assertEqual(await self.repository.get("c"), {})

    async def test_delete_chat_keeps_other_scopes(self):
        await self.repository.set("c", "supervisor", TOOLS_MODEL)
        await self.repository.set("d", "supervisor", TOOLS_MODEL)
        await self.repository.set(GLOBAL_SCOPE, "supervisor", TOOLS_MODEL)
        await self.repository.delete_chat("c")
        await self.repository.delete_chat(GLOBAL_SCOPE)
        self.assertEqual(await self.repository.get("c"), {})
        self.assertEqual(len(await self.repository.get("d")), 1)
        self.assertEqual(len(await self.repository.get(GLOBAL_SCOPE)), 1)

    async def test_settings_survive_reopening(self):
        await self.repository.set("c", "summarizer", TEXT_MODEL)
        reopened = await ModelSettingsRepository.open(self.database)
        try:
            self.assertEqual(await reopened.get("c"), {"summarizer": TEXT_MODEL})
        finally:
            await reopened.close()


class EffortTests(_Base):
    async def supervisor(self, chat_id="c"):
        with patch.dict(MODEL_ROLES["supervisor"], {"reasoning_effort": None}):
            return (await self.resolver.resolve(chat_id))["supervisor"]

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.repository.set(GLOBAL_SCOPE, "supervisor", TOOLS_MODEL)

    async def test_without_choice_model_uses_its_default(self):
        entry = await self.supervisor()
        self.assertIsNone(entry["effort"])
        self.assertEqual(entry["effort_source"], "model")
        self.assertEqual(entry["default_effort"], "low")
        self.assertEqual(entry["supported_efforts"], ["none", "low", "high"])

    async def test_session_beats_global(self):
        await self.repository.set_effort(GLOBAL_SCOPE, "supervisor", "low")
        entry = await self.supervisor()
        self.assertEqual((entry["effort"], entry["effort_source"]), ("low", "global"))
        await self.repository.set_effort("c", "supervisor", "high")
        entry = await self.supervisor()
        self.assertEqual((entry["effort"], entry["effort_source"]), ("high", "session"))
        self.assertEqual((await self.supervisor("d"))["effort"], "low")

    async def test_configured_effort_is_the_last_option(self):
        with patch.dict(MODEL_ROLES["supervisor"], {"reasoning_effort": "high"}):
            entry = (await self.resolver.resolve("c"))["supervisor"]
        self.assertEqual((entry["effort"], entry["effort_source"]), ("high", "default"))
        with patch.dict(MODEL_ROLES["supervisor"], {"reasoning_effort": "high"}), patch.dict(
            "os.environ", {"REASONING_SUPERVISOR": "high"}
        ):
            self.assertEqual((await self.resolver.resolve("c"))["supervisor"]["effort_source"], "env")

    async def test_effort_the_model_lacks_is_skipped_and_marked(self):
        await self.repository.set_effort("c", "supervisor", "max")
        await self.repository.set_effort(GLOBAL_SCOPE, "supervisor", "high")
        entry = await self.supervisor()
        self.assertEqual((entry["effort"], entry["effort_source"]), ("high", "global"))
        self.assertEqual(entry["effort_unavailable"], ["max"])

    async def test_changing_model_drops_unsupported_effort(self):
        await self.repository.set_effort("c", "supervisor", "high")
        await self.repository.set("c", "supervisor", CATALOG_LIMITED)
        entry = await self.supervisor()
        self.assertIsNone(entry["effort"])
        self.assertEqual(entry["effort_unavailable"], ["high"])

    async def test_without_catalog_choice_is_respected(self):
        await self.repository.set_effort("c", "supervisor", "xhigh")
        self.catalog.list_models.side_effect = CatalogUnavailable
        self.assertEqual((await self.supervisor())["effort"], "xhigh")

    async def test_check_effort(self):
        await self.resolver.check_effort("c", "supervisor", "high")
        with self.assertRaisesRegex(InvalidModel, "no admite"):
            await self.resolver.check_effort("c", "supervisor", "max")
        await self.repository.set("c", "supervisor", OTHER_TOOLS_MODEL)
        with self.assertRaisesRegex(InvalidModel, "no admite"):
            await self.resolver.check_effort("c", "supervisor", "high")

    async def test_set_none_and_delete_chat(self):
        await self.repository.set_effort("c", "supervisor", "high")
        await self.repository.set_effort("d", "supervisor", "low")
        await self.repository.set_effort(GLOBAL_SCOPE, "supervisor", "low")
        await self.repository.set_effort("c", "summarizer", "low")
        await self.repository.set_effort("c", "summarizer", None)
        self.assertEqual(await self.repository.get_efforts("c"), {"supervisor": "high"})
        await self.repository.delete_chat("c")
        self.assertEqual(await self.repository.get_efforts("c"), {})
        self.assertEqual(await self.repository.get_efforts("d"), {"supervisor": "low"})
        self.assertEqual(await self.repository.get_efforts(GLOBAL_SCOPE), {"supervisor": "low"})


class ResolutionTests(_Base):
    async def test_every_role_is_resolved(self):
        self.assertEqual(set(await self.resolver.resolve("c")), set(MODEL_ROLES))

    async def test_default_when_nothing_is_saved(self):
        with patch.dict("os.environ"):
            __import__("os").environ.pop("MODEL_SUPERVISOR", None)
            entry = (await self.resolver.resolve("c"))["supervisor"]
        self.assertEqual(entry["model"], MODEL_ROLES["supervisor"]["model"])
        self.assertEqual(entry["source"], "default")

    async def test_env_is_reported_as_origin(self):
        with patch.dict("os.environ", {"MODEL_SUPERVISOR": "x"}):
            self.assertEqual((await self.resolver.resolve("c"))["supervisor"]["source"], "env")

    async def test_global_beats_default_and_session_beats_global(self):
        await self.repository.set(GLOBAL_SCOPE, "supervisor", TOOLS_MODEL)
        entry = (await self.resolver.resolve("c"))["supervisor"]
        self.assertEqual((entry["model"], entry["source"]), (TOOLS_MODEL, "global"))

        await self.repository.set("c", "supervisor", OTHER_TOOLS_MODEL)
        entry = (await self.resolver.resolve("c"))["supervisor"]
        self.assertEqual((entry["model"], entry["source"]), (OTHER_TOOLS_MODEL, "session"))
        self.assertEqual((await self.resolver.resolve("d"))["supervisor"]["model"], TOOLS_MODEL)

    async def test_capabilities_come_from_catalog(self):
        await self.repository.set("c", "supervisor", OTHER_TOOLS_MODEL)
        entry = (await self.resolver.resolve("c"))["supervisor"]
        self.assertIs(entry["supports_temperature"], False)
        self.assertIs(entry["supports_reasoning"], False)

    async def test_missing_model_falls_back_and_is_marked(self):
        await self.repository.set("c", "supervisor", "acme/retirado")
        with self.assertLogs("core.model_settings", level="WARNING"):
            entry = (await self.resolver.resolve("c"))["supervisor"]
        self.assertEqual(entry["model"], MODEL_ROLES["supervisor"]["model"])
        self.assertEqual(entry["unavailable"], ["acme/retirado"])

    async def test_model_that_stopped_fitting_is_not_used(self):
        await self.repository.set("c", "supervisor", TEXT_MODEL)
        await self.repository.set(GLOBAL_SCOPE, "supervisor", TOOLS_MODEL)
        with self.assertLogs("core.model_settings", level="WARNING"):
            entry = (await self.resolver.resolve("c"))["supervisor"]
        self.assertEqual((entry["model"], entry["source"]), (TOOLS_MODEL, "global"))
        self.assertEqual(entry["unavailable"], [TEXT_MODEL])

    async def test_without_catalog_choice_is_respected(self):
        await self.repository.set("c", "supervisor", "acme/sin-comprobar")
        self.catalog.list_models.side_effect = CatalogUnavailable
        entry = (await self.resolver.resolve("c"))["supervisor"]
        self.assertEqual(entry["model"], "acme/sin-comprobar")
        self.assertIsNone(entry["supports_temperature"])

    async def test_check_model(self):
        await self.resolver.check_model("supervisor", TOOLS_MODEL)
        await self.resolver.check_model("summarizer", TEXT_MODEL)
        with self.assertRaisesRegex(InvalidModel, "no existe"):
            await self.resolver.check_model("supervisor", "acme/nada")
        with self.assertRaisesRegex(InvalidModel, "requisitos"):
            await self.resolver.check_model("supervisor", TEXT_MODEL)


class BuildModelTests(unittest.TestCase):
    def kwargs(self, role="context_classifier", **arguments):
        with patch("core.llm.OpenRouterChat") as chat, patch.dict(
            MODEL_ROLES[role], {"reasoning_effort": "medium"}
        ):
            build_model(role, **arguments)
        return chat.call_args.kwargs

    def test_without_resolution_keeps_role_values(self):
        kwargs = self.kwargs()
        self.assertEqual(kwargs["model"], MODEL_ROLES["context_classifier"]["model"])
        self.assertIn("temperature", kwargs)
        self.assertEqual(kwargs["reasoning_effort"], "medium")

    def test_uses_resolved_model(self):
        self.assertEqual(self.kwargs(resolved={"model": TOOLS_MODEL})["model"], TOOLS_MODEL)

    def test_tags_role_and_model(self):
        kwargs = self.kwargs(resolved={"model": TOOLS_MODEL})
        self.assertEqual(kwargs["tags"], ["role:context_classifier", f"model:{TOOLS_MODEL}"])

    def test_omits_unsupported_parameters(self):
        kwargs = self.kwargs(resolved={
            "model": TOOLS_MODEL,
            "supports_temperature": False,
            "supports_reasoning": False,
        })
        self.assertNotIn("temperature", kwargs)
        self.assertNotIn("reasoning_effort", kwargs)

    def test_output_cap_only_on_bounded_roles(self):
        self.assertEqual(self.kwargs()["max_tokens"], MODEL_ROLES["context_classifier"]["max_tokens"])
        self.assertGreater(MODEL_ROLES["summarizer"]["max_tokens"], 0)
        for role in ("supervisor", "subagent_light", "subagent_heavy"):
            self.assertNotIn("max_tokens", self.kwargs(role=role))

    def test_resolved_effort_replaces_the_role_value(self):
        self.assertEqual(self.kwargs(resolved={"model": TOOLS_MODEL, "effort": "high"})["reasoning_effort"], "high")

    def test_none_effort_is_sent_as_a_level(self):
        self.assertEqual(self.kwargs(resolved={"model": TOOLS_MODEL, "effort": "none"})["reasoning_effort"], "none")

    def test_effort_without_usable_level_is_omitted(self):
        self.assertNotIn("reasoning_effort", self.kwargs(resolved={"model": TOOLS_MODEL, "effort": None}))

    def test_unknown_capabilities_are_sent(self):
        kwargs = self.kwargs(resolved={
            "model": TOOLS_MODEL,
            "supports_temperature": None,
            "supports_reasoning": None,
        })
        self.assertIn("temperature", kwargs)
        self.assertIn("reasoning_effort", kwargs)

    def test_model_for_turn_falls_back(self):
        self.assertEqual(model_for_turn({"configurable": {}}, "summarizer", "arranque"), "arranque")
        config = {"configurable": {"models": {}}}
        self.assertEqual(model_for_turn(config, "summarizer", "arranque"), "arranque")
        with patch("core.llm.build_model", return_value="resuelto") as builder:
            config = {"configurable": {"models": {"summarizer": {"model": TEXT_MODEL}}}}
            self.assertEqual(model_for_turn(config, "summarizer", "arranque"), "resuelto")
        builder.assert_called_once_with("summarizer", resolved={"model": TEXT_MODEL})


class SupervisorRebuildTests(unittest.TestCase):
    def setUp(self):
        with patch("core.graphs.session.build_model"), patch("core.graphs.session.ChainBuilder"):
            self.graph = SessionStudyGraph()

    def models(self, supervisor=TOOLS_MODEL, summarizer=TEXT_MODEL):
        return {"supervisor": {"model": supervisor}, "summarizer": {"model": summarizer}}

    def test_rebuilds_only_if_supervisor_models_change(self):
        with patch("core.graphs.session.SupervisorBuilder") as builder:
            builder.return_value.build.side_effect = lambda: object()
            first = self.graph._get_or_build_supervisor("s", "plan", self.models())
            same = self.graph._get_or_build_supervisor("s", "plan", self.models(summarizer="otro"))
            self.assertIs(first, same)

            changed = self.graph._get_or_build_supervisor("s", "plan", self.models(OTHER_TOOLS_MODEL))
            self.assertIsNot(first, changed)
        self.assertEqual(len(self.graph.supervisors), 1)

    def test_rebuilds_if_effort_changes(self):
        with patch("core.graphs.session.SupervisorBuilder") as builder:
            builder.return_value.build.side_effect = lambda: object()
            models = {"supervisor": {"model": TOOLS_MODEL, "effort": "low"}}
            first = self.graph._get_or_build_supervisor("s", "plan", models)
            models = {"supervisor": {"model": TOOLS_MODEL, "effort": "high"}}
            self.assertIsNot(first, self.graph._get_or_build_supervisor("s", "plan", models))

    def test_cached_lookup_ignores_stale_models(self):
        with patch("core.graphs.session.SupervisorBuilder") as builder:
            builder.return_value.build.side_effect = lambda: object()
            supervisor = self.graph._get_or_build_supervisor("s", "plan", self.models())
        self.assertIs(self.graph._get_cached_supervisor("s", self.models()), supervisor)
        self.assertIsNone(self.graph._get_cached_supervisor("s", self.models(OTHER_TOOLS_MODEL)))

    def test_eviction_and_removal_forget_fingerprints(self):
        self.graph.supervisors_cache_max_entries = 1
        with patch("core.graphs.session.SupervisorBuilder") as builder:
            builder.return_value.build.side_effect = lambda: object()
            self.graph._get_or_build_supervisor("a", "plan", self.models())
            self.graph._get_or_build_supervisor("b", "plan", self.models())
        self.assertEqual(set(self.graph.supervisor_fingerprints), {"b"})
        self.graph.remove_session_cache_entry("b")
        self.assertEqual(self.graph.supervisor_fingerprints, {})


class _Sender:
    async def send_json(self, payload):
        pass


class _ToolModel(GenericFakeChatModel):
    # El modelo simulado no puede hacer stream de un mensaje vacío con tool_calls.
    disable_streaming: bool = True

    def bind_tools(self, tools, **kwargs):
        return self


class ContextModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_uses_models_of_the_new_turn(self):
        plan = StudyPlan.model_validate({
            "subject": "derivadas",
            "syllabus": ["Definición"],
            "supervisor_focus": "Principiante.",
            "archetypes": [{"archetype": "explicador", "focus": "Derivadas."}],
        })
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with (
            patch("core.graphs.session.build_model"),
            patch("core.graphs.context.build_model"),
            patch("core.graphs.context.build_tools", return_value=[]),
        ):
            study_graph = StudyGraph()
        used = []

        def build_structured_chain(model, *_, **__):
            used.append(model)
            return SimpleNamespace(ainvoke=AsyncMock(return_value=plan))

        study_graph.context.chain_builder = SimpleNamespace(build_structured_chain=build_structured_chain)
        checkpointer = await open_checkpointer(Path(directory.name) / "study.db")
        graph = await study_graph.compile_graph(checkpointer=checkpointer)
        self.addAsyncCleanup(checkpointer.conn.close)
        agent = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
            {"structured_response": ContextClassification(approved_context=False, questions=["¿Nivel?"])},
            {"structured_response": ContextClassification(
                approved_context=True,
                questions=None,
                session_objetive="Parcial",
                session_context="Derivadas",
                previous_level="principiante",
                title_session="Derivadas",
            )},
        ]))

        def config(name):
            models = {"factory": {"model": name}, "context_classifier": {"model": name}}
            return {"configurable": {"thread_id": "t", "models": models}}

        with (
            patch("core.graphs.context.create_agent", return_value=agent) as create_agent,
            patch("core.llm.build_model", side_effect=lambda role, resolved: resolved["model"]),
        ):
            await execute_astream_graph(graph, _Sender(), {"messages": [HumanMessage(content="hola")]}, config("primero"))
            await execute_astream_graph(graph, _Sender(), Command(resume="desde cero"), config("segundo"))

        self.assertEqual(create_agent.call_args_list[0].args[0], "primero")
        self.assertEqual(create_agent.call_args_list[-1].args[0], "segundo")
        self.assertEqual(used, ["segundo"])


class SessionTurnModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_turn_rebuilds_supervisor_with_new_model(self):
        with patch("core.graphs.session.build_model"), patch("core.graphs.session.ChainBuilder"):
            graph = SessionStudyGraph()
        built = []

        class _Builder:
            def __init__(self, study_plan, models=None):
                built.append((models or {}).get("supervisor", {}).get("model"))

            def build(self):
                return SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="ok")]}))

        with patch("core.graphs.session.SupervisorBuilder", _Builder):
            graph._get_or_build_supervisor("t", "plan", {"supervisor": {"model": "uno"}})
            graph._get_or_build_supervisor("t", "plan", {"supervisor": {"model": "uno"}})
            graph._get_or_build_supervisor("t", "plan", {"supervisor": {"model": "dos"}})
        self.assertEqual(built, ["uno", "dos"])


class PipelineModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_uses_session_model(self):
        from documents.pipeline import DocumentPipeline

        with patch("documents.pipeline.build_model", return_value="arranque"):
            without_resolver = DocumentPipeline(MagicMock(), MagicMock())
            self.assertEqual(await without_resolver._summary_model("c"), "arranque")

            resolver = SimpleNamespace(resolve=AsyncMock(return_value={"summarizer": {"model": TEXT_MODEL}}))
            pipeline = DocumentPipeline(MagicMock(), MagicMock(), resolver)
        with patch("documents.pipeline.build_model", return_value="resuelto") as builder:
            self.assertEqual(await pipeline._summary_model("c"), "resuelto")
        resolver.resolve.assert_awaited_once_with("c")
        builder.assert_called_once_with("summarizer", resolved={"model": TEXT_MODEL})


class SettingsEndpointTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        self.settings = SimpleNamespace(set=AsyncMock(), set_effort=AsyncMock(), delete_chat=AsyncMock())
        self.resolver = SimpleNamespace(
            resolve=AsyncMock(return_value={"supervisor": {"model": TOOLS_MODEL}}),
            check_model=AsyncMock(),
            check_effort=AsyncMock(),
        )
        app.state.model_settings = self.settings
        app.state.model_resolver = self.resolver
        self.client = TestClient(app)

    def test_global_get_and_put(self):
        self.assertEqual(self.client.get("/models/settings").json()["models"]["supervisor"]["model"], TOOLS_MODEL)
        response = self.client.put("/models/settings", json={"role": "supervisor", "model": TOOLS_MODEL})
        self.assertEqual(response.status_code, 200)
        self.settings.set.assert_awaited_once_with(GLOBAL_SCOPE, "supervisor", TOOLS_MODEL)

    def test_session_put_uses_its_scope(self):
        response = self.client.put("/chats/thread/models", json={"role": "supervisor", "model": TOOLS_MODEL})
        self.assertEqual(response.status_code, 200)
        self.settings.set.assert_awaited_once_with("thread", "supervisor", TOOLS_MODEL)
        self.resolver.resolve.assert_awaited_with("thread")
        self.client.get("/chats/thread/models")
        self.resolver.resolve.assert_awaited_with("thread")

    def test_null_removes_without_catalog(self):
        response = self.client.put("/chats/thread/models", json={"role": "supervisor", "model": None})
        self.assertEqual(response.status_code, 200)
        self.resolver.check_model.assert_not_awaited()
        self.settings.set.assert_awaited_once_with("thread", "supervisor", None)

    def test_rejects_invalid_values_without_saving(self):
        self.resolver.check_model.side_effect = InvalidModel("El modelo no existe en el catálogo.")
        for payload in ({"role": "supervisor", "model": "acme/nada"}, {"role": "otro", "model": TOOLS_MODEL}):
            self.assertEqual(self.client.put("/models/settings", json=payload).status_code, 422)
        self.settings.set.assert_not_awaited()

    def test_unavailable_catalog_is_503(self):
        self.resolver.check_model.side_effect = CatalogUnavailable
        response = self.client.put("/models/settings", json={"role": "supervisor", "model": TOOLS_MODEL})
        self.assertEqual(response.status_code, 503)
        self.settings.set.assert_not_awaited()

    def test_global_and_session_effort_use_their_scope(self):
        payload = {"role": "supervisor", "effort": "high"}
        self.assertEqual(self.client.put("/models/effort", json=payload).status_code, 200)
        self.resolver.check_effort.assert_awaited_with(None, "supervisor", "high")
        self.settings.set_effort.assert_awaited_with(GLOBAL_SCOPE, "supervisor", "high")
        self.assertEqual(self.client.put("/chats/thread/effort", json=payload).status_code, 200)
        self.resolver.check_effort.assert_awaited_with("thread", "supervisor", "high")
        self.settings.set_effort.assert_awaited_with("thread", "supervisor", "high")

    def test_null_effort_removes_without_catalog(self):
        response = self.client.put("/chats/thread/effort", json={"role": "supervisor", "effort": None})
        self.assertEqual(response.status_code, 200)
        self.resolver.check_effort.assert_not_awaited()
        self.settings.set_effort.assert_awaited_once_with("thread", "supervisor", None)

    def test_invalid_effort_is_not_saved(self):
        self.resolver.check_effort.side_effect = InvalidModel("El modelo no admite ese nivel de razonamiento.")
        for payload in ({"role": "supervisor", "effort": "max"}, {"role": "otro", "effort": "low"}):
            self.assertEqual(self.client.put("/models/effort", json=payload).status_code, 422)
        self.resolver.check_effort.side_effect = CatalogUnavailable
        response = self.client.put("/models/effort", json={"role": "supervisor", "effort": "low"})
        self.assertEqual(response.status_code, 503)
        self.settings.set_effort.assert_not_awaited()

    def test_session_cannot_use_global_scope(self):
        with patch("api.routes.validate_chat_id"):
            response = self.client.put("/chats/global/models", json={"role": "supervisor", "model": TOOLS_MODEL})
        self.assertEqual(response.status_code, 400)
        self.settings.set.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
