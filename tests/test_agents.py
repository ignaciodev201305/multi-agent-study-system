"""Pruebas del catálogo, el plan de estudio y el supervisor con subagentes como tools."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.errors import GraphInterrupt
from pydantic import ValidationError

from core.agents.builder import (
    ADDED_BLOCKS_NOTE,
    BOARD_CONTEXT_NOTE,
    SPECIALIST_FAILED_NOTE,
    SUPERVISOR_TOOLS,
    SupervisorBuilder,
    _subagent_tool,
)
from core.agents.catalog import ARCHETYPES, archetype_prompt, catalog_for_factory
from core.board import MathBlock, add_block
from core.prompts.context import STUDY_PLAN_PROMPT
from core.prompts.session import MATH_VERIFY_RULE
from core.schemas import StudyPlan
from core.tools import TOOL_NAMES, TurnContext
from core.tools.documents import get_study_material, search_documents
from core.tools.progress import record_progress


class _ToolModel(GenericFakeChatModel):
    """Modelo simulado que acepta bind_tools y responde con mensajes guionados."""

    def bind_tools(self, tools, **kwargs):
        return self


def _plan(**overrides):
    values = {
        "subject": "derivadas",
        "syllabus": ["Definición", "Regla de la cadena"],
        "supervisor_focus": "Alumno principiante que prepara un parcial.",
        "archetypes": [
            {"archetype": "explicador", "focus": "Derivadas desde cero."},
            {"archetype": "evaluador", "focus": "Preguntas tipo parcial."},
        ],
    }
    values.update(overrides)
    return StudyPlan.model_validate(values)


STUDY_MAP = {
    "document_id": "m1",
    "document_type": "study_map",
    "original_filename": "guia.pdf",
    "summary": {
        "title": "Guía de derivadas",
        "summary": "Derivadas.",
        "topics": ["Definición"],
        "exercises": ["Deriva x^2", "Deriva sin(x)"],
    },
}
RAG_DOCUMENT = {
    "document_id": "r1",
    "document_type": "rag",
    "original_filename": "libro.pdf",
    "summary": {"title": "Cálculo I", "summary": "Libro."},
}


class _FailingModel(_ToolModel):
    """Responde su guion y luego falla con el error indicado."""
    error: BaseException

    def _generate(self, *args, **kwargs):
        try:
            return super()._generate(*args, **kwargs)
        except (StopIteration, RuntimeError):
            raise self.error from None


class ToolNamesTests(unittest.TestCase):
    def test_catalog_only_uses_registered_tools(self):
        for archetype in ARCHETYPES.values():
            self.assertLessEqual(set(archetype.tools), TOOL_NAMES, archetype.name)
        self.assertLessEqual(set(SUPERVISOR_TOOLS), TOOL_NAMES)

    def test_exercises_belong_to_the_trainer(self):
        self.assertIn("exercise", ARCHETYPES["entrenador_de_ejercicios"].prompt)
        self.assertNotIn("exercise", ARCHETYPES["evaluador"].prompt)

    def test_supervisor_can_verify_math(self):
        self.assertIn("math", SUPERVISOR_TOOLS)

    def test_evaluator_and_analyst_tools(self):
        self.assertIn("search_documents", ARCHETYPES["evaluador"].tools)
        self.assertIn("board_add", ARCHETYPES["analista_de_documentos"].tools)


class StudyPlanTests(unittest.TestCase):
    def test_rejects_repeated_or_unknown_archetypes(self):
        with self.assertRaises(ValidationError):
            _plan(archetypes=[
                {"archetype": "explicador", "focus": "a"},
                {"archetype": "explicador", "focus": "b"},
            ])
        with self.assertRaises(ValidationError):
            _plan(archetypes=[{"archetype": "inventado", "focus": "a"}])

    def test_catalog_lists_every_archetype(self):
        listing = catalog_for_factory()
        for name in ARCHETYPES:
            self.assertIn(name, listing)

    def test_plan_accepts_code_tutor(self):
        plan = _plan(archetypes=[{"archetype": "tutor_de_codigo", "focus": "Python desde cero."}])
        self.assertEqual(plan.archetypes[0].archetype, "tutor_de_codigo")

    def test_code_tutor_works_without_server_execution(self):
        tutor = ARCHETYPES["tutor_de_codigo"]
        self.assertEqual(set(tutor.tools), {"board_add", "math"})
        self.assertEqual(tutor.tier, "heavy")
        self.assertIn("navegador del alumno", tutor.prompt)
        self.assertIn("PIZARRA", archetype_prompt(tutor, "foco"))
        self.assertIn("tutor_de_codigo", STUDY_PLAN_PROMPT)


class SupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_consults_subagent_and_records_progress(self):
        supervisor_model = _ToolModel(messages=iter([
            AIMessage(content="", tool_calls=[{
                "name": "explicador",
                "args": {"request": "Explica la regla de la cadena a un principiante."},
                "id": "call-1",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "record_progress",
                "args": {"topic_number": 1, "status": "dominado", "evidence": "Respondió bien."},
                "id": "call-2",
            }]),
            AIMessage(content="Aquí va la explicación del especialista."),
        ]))
        subagent_model = _ToolModel(messages=iter([AIMessage(content="La regla de la cadena: $f(g(x))' = f'(g(x))g'(x)$.")]))
        models = {"supervisor": supervisor_model, "subagent_light": subagent_model}

        with (
            patch("core.agents.builder.build_model", side_effect=lambda role, **_: models[role]),
            patch("core.agents.builder.build_tools", side_effect=_tools_without_web),
        ):
            supervisor = SupervisorBuilder(_plan(archetypes=[{"archetype": "explicador", "focus": "Derivadas."}])).build()

        context = TurnContext(session_id="s", documents=[STUDY_MAP], study_plan=_plan())
        result = await supervisor.ainvoke(
            {"messages": [SystemMessage(content="contexto"), HumanMessage(content="hola")]},
            context=context,
        )

        tool_messages = [message for message in result["messages"] if message.type == "tool"]
        self.assertIn("regla de la cadena", tool_messages[0].content)
        self.assertEqual(context.progress_updates, [{
            "topic_number": 1,
            "topic": "Definición",
            "status": "dominado",
            "evidence": "Respondió bien.",
        }])
        self.assertEqual(result["messages"][-1].content, "Aquí va la explicación del especialista.")

    def supervisor_with_failing_specialist(self, error):
        call = AIMessage(content="", tool_calls=[{
            "name": "explicador",
            "args": {"request": "Explica."},
            "id": "call-1",
        }])
        supervisor_model = _ToolModel(messages=iter([call, AIMessage(content="Sigo yo.")]))
        add_block = AIMessage(content="", tool_calls=[{
            "name": "board_add",
            "args": {"block": {"kind": "math", "latex": "x^2"}, "title": "Parcial"},
            "id": "call-2",
        }])
        models = {
            "supervisor": supervisor_model,
            "subagent_light": _FailingModel(messages=iter([add_block]), error=error),
        }
        with (
            patch("core.agents.builder.build_model", side_effect=lambda role, **_: models[role]),
            patch("core.agents.builder.build_tools", side_effect=_tools_without_web),
        ):
            return SupervisorBuilder(_plan(archetypes=[{"archetype": "explicador", "focus": "Derivadas."}])).build()

    async def test_failed_specialist_keeps_turn_and_blocks(self):
        supervisor = self.supervisor_with_failing_specialist(RuntimeError("proveedor caído"))
        context = TurnContext(session_id="s", study_plan=_plan())
        with self.assertLogs("core.agents.builder", level="ERROR"):
            result = await supervisor.ainvoke({"messages": [HumanMessage(content="hola")]}, context=context)

        self.assertEqual(result["messages"][-1].content, "Sigo yo.")
        note = next(message for message in result["messages"] if message.type == "tool").content
        self.assertIn(SPECIALIST_FAILED_NOTE, note)
        self.assertIn(ADDED_BLOCKS_NOTE, note)
        self.assertIn("Parcial", note)
        self.assertEqual(len(context.board.blocks), 1)

    async def test_specialist_receives_board_inventory(self):
        agent = SimpleNamespace(ainvoke=AsyncMock(return_value={"messages": [AIMessage(content="Listo.")]}))
        with (
            patch("core.agents.builder.build_model"),
            patch("core.agents.builder.build_tools", return_value=[]),
            patch("core.agents.builder.create_agent", return_value=agent),
        ):
            consult = _subagent_tool(ARCHETYPES["explicador"], "Derivadas.", {})

        context = TurnContext("s")
        await consult.coroutine(request="Explica.", runtime=_Runtime(context))
        self.assertEqual(agent.ainvoke.await_args.args[0]["messages"][0].content, "Explica.")

        add_block(context.board, MathBlock(kind="math", latex="x^2"), author="explicador", title="Cuadrado")
        await consult.coroutine(request="Explica.", runtime=_Runtime(context))
        sent = agent.ainvoke.await_args.args[0]["messages"][0].content
        self.assertTrue(sent.startswith("Explica."))
        self.assertIn(BOARD_CONTEXT_NOTE, sent)
        self.assertIn("- b1 (math): Cuadrado", sent)

    async def test_graph_interrupt_is_not_treated_as_failure(self):
        agent = SimpleNamespace(ainvoke=AsyncMock(side_effect=GraphInterrupt(())))
        with (
            patch("core.agents.builder.build_model"),
            patch("core.agents.builder.build_tools", return_value=[]),
            patch("core.agents.builder.create_agent", return_value=agent),
        ):
            consult = _subagent_tool(ARCHETYPES["explicador"], "Derivadas.", {})
        with self.assertRaises(GraphInterrupt):
            await consult.coroutine(request="Explica.", runtime=_Runtime(TurnContext("s")))

    def test_system_prompt_includes_plan(self):
        prompt = SupervisorBuilder(_plan()).system_prompt()
        self.assertIn("1. Definición", prompt)
        self.assertIn("2. Regla de la cadena", prompt)
        self.assertIn("- evaluador:", prompt)
        self.assertIn("$...$", prompt)
        self.assertIn(MATH_VERIFY_RULE, prompt)

    def test_only_specialists_with_math_get_the_verify_rule(self):
        for name, archetype in ARCHETYPES.items():
            with (
                patch("core.agents.builder.build_tools", return_value=[]),
                patch("core.agents.builder.create_agent") as create_agent,
            ):
                _subagent_tool(archetype, "Foco.", {})
            prompt = create_agent.call_args.kwargs["system_prompt"]
            self.assertEqual(MATH_VERIFY_RULE in prompt, "math" in archetype.tools, name)


class _RecordingToolModel(_ToolModel):
    """Modelo simulado que guarda los nombres de las tools de cada bind_tools."""

    bound: list = []

    def bind_tools(self, tools, **kwargs):
        self.bound.append({getattr(tool, "name", None) or tool.get("name") or tool["function"]["name"] for tool in tools})
        return self


class DeepSupervisorTests(unittest.IsolatedAsyncioTestCase):
    def build(self, messages, deep):
        model = _RecordingToolModel(messages=iter(messages), bound=[])
        with (
            patch("core.agents.builder.build_model", return_value=model),
            patch("core.agents.builder.build_tools", side_effect=_tools_without_web),
        ):
            plan = _plan(archetypes=[{"archetype": "explicador", "focus": "Derivadas."}])
            return model, SupervisorBuilder(plan, deep=deep).build()

    async def test_deep_supervisor_plans_and_delegates_read_only_research(self):
        model, supervisor = self.build([
            AIMessage(content="", tool_calls=[{
                "name": "write_todos",
                "args": {"todos": [{"content": "Integrar por partes", "status": "in_progress"}]},
                "id": "call-1",
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "task",
                "args": {"description": "Calcula la integral de x*e^x.", "subagent_type": "general-purpose"},
                "id": "call-2",
            }]),
            AIMessage(content="Informe: (x-1)e^x + C."),
            AIMessage(content="Respuesta final."),
        ], deep=True)

        result = await supervisor.ainvoke(
            {"messages": [SystemMessage(content="contexto"), HumanMessage(content="integra")]},
            context=TurnContext(session_id="s", study_plan=_plan()),
        )

        self.assertEqual(result["messages"][-1].content, "Respuesta final.")
        self.assertIn("Informe: (x-1)e^x + C.", [message.content for message in result["messages"]])
        main_tools = model.bound[0]
        self.assertLessEqual({"write_todos", "write_file", "read_file", "task", "explicador", "board_add"}, main_tools)
        self.assertNotIn("execute", main_tools)
        research_tools = next(names for names in model.bound if "task" not in names)
        self.assertIn("math", research_tools)
        self.assertFalse({"board_add", "board_edit", "record_progress", "execute"} & research_tools)

    async def test_normal_supervisor_has_no_deep_tools(self):
        model, supervisor = self.build([AIMessage(content="Hola.")], deep=False)
        await supervisor.ainvoke(
            {"messages": [HumanMessage(content="hola")]},
            context=TurnContext(session_id="s", study_plan=_plan()),
        )
        self.assertFalse({"write_todos", "write_file", "task"} & model.bound[0])


def _tools_without_web(names):
    from core.tools import build_tools
    return build_tools([name for name in names if name != "web_search"])


class _FakeStore:
    def __init__(self):
        self.filters = []

    def similarity_search(self, query, k, filter):
        self.filters.append(filter)
        return [Document(page_content="La derivada mide el cambio.", metadata={"document_id": "r1", "pages": "12,13"})]


class _Runtime:
    def __init__(self, context):
        self.context = context


class RecordProgressTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self):
        return _Runtime(TurnContext("s", study_plan=_plan()))

    async def record(self, runtime, topic_number, status="dominado", evidence="Bien."):
        return await record_progress.coroutine(
            topic_number=topic_number, status=status, evidence=evidence, runtime=runtime
        )

    async def test_invalid_number_returns_numbered_syllabus(self):
        runtime = self.runtime()
        for number in (0, 3, -1):
            result = await self.record(runtime, number)
            self.assertIn("no existe", result)
            self.assertIn("2. Regla de la cadena", result)
        self.assertEqual(runtime.context.progress_updates, [])

    async def test_uses_syllabus_wording(self):
        runtime = self.runtime()
        self.assertIn("Regla de la cadena", await self.record(runtime, 2))
        self.assertEqual(runtime.context.progress_updates[0]["topic"], "Regla de la cadena")

    async def test_last_evaluation_of_topic_wins(self):
        runtime = self.runtime()
        await self.record(runtime, 1, status="dominado", evidence="Primera.")
        await self.record(runtime, 2, status="en_progreso")
        await self.record(runtime, 1, status="con_dificultad", evidence="Última.")
        updates = runtime.context.progress_updates
        self.assertEqual([update["topic_number"] for update in updates], [2, 1])
        self.assertEqual((updates[1]["status"], updates[1]["evidence"]), ("con_dificultad", "Última."))

    async def test_update_list_is_shared_with_subagent_copies(self):
        context = TurnContext("s", study_plan=_plan())
        await self.record(_Runtime(replace(context, author="evaluador")), 1)
        self.assertEqual(len(context.progress_updates), 1)


class DocumentToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_study_material_returns_requested_exercise(self):
        result = await get_study_material.coroutine(
            section="ejercicios", number=2, runtime=_Runtime(TurnContext("s", [STUDY_MAP]))
        )
        self.assertIn("2. Deriva sin(x)", result)
        self.assertNotIn("Deriva x^2", result)

    async def test_study_material_without_maps(self):
        result = await get_study_material.coroutine(section="temas", runtime=_Runtime(TurnContext("s", [])))
        self.assertIn("No hay mapas", result)

    async def test_search_cites_title_and_pages(self):
        with patch("core.tools.documents._get_vector_store", return_value=_FakeStore()):
            result = await search_documents.coroutine(
                query="derivada", runtime=_Runtime(TurnContext("s", [RAG_DOCUMENT]))
            )
        self.assertIn('documento="Cálculo I" páginas 12-13', result)

    async def test_search_filters_by_document(self):
        other = {**RAG_DOCUMENT, "document_id": "r2", "original_filename": "fisica.pdf", "summary": {"title": "Física"}}
        store = _FakeStore()
        with patch("core.tools.documents._get_vector_store", return_value=store):
            await search_documents.coroutine(
                query="x", document="cálculo", runtime=_Runtime(TurnContext("s", [RAG_DOCUMENT, other]))
            )
            await search_documents.coroutine(query="x", runtime=_Runtime(TurnContext("s", [RAG_DOCUMENT, other])))
        self.assertEqual(store.filters[0], {"$and": [{"session_id": "s"}, {"document_id": {"$in": ["r1"]}}]})
        self.assertEqual(store.filters[1], {"session_id": "s"})

    async def test_unknown_document_lists_available(self):
        runtime = _Runtime(TurnContext("s", [RAG_DOCUMENT, STUDY_MAP]))
        with patch("core.tools.documents._get_vector_store") as store:
            result = await search_documents.coroutine(query="x", document="química", runtime=runtime)
        store.assert_not_called()
        self.assertIn("Cálculo I", result)
        result = await get_study_material.coroutine(section="temas", document="química", runtime=runtime)
        self.assertIn("No hay un documento", result)

    async def test_study_material_filters_by_document(self):
        other = {**STUDY_MAP, "original_filename": "otra.pdf", "summary": {"title": "Otra", "exercises": ["Integra x"]}}
        result = await get_study_material.coroutine(
            section="ejercicios", number=1, document="otra", runtime=_Runtime(TurnContext("s", [STUDY_MAP, other]))
        )
        self.assertIn("Integra x", result)
        self.assertNotIn("Deriva x^2", result)

    async def test_search_without_rag_documents_does_not_touch_store(self):
        with patch("core.tools.documents._get_vector_store") as store:
            result = await search_documents.coroutine(
                query="x", runtime=_Runtime(TurnContext("s", [STUDY_MAP]))
            )
        store.assert_not_called()
        self.assertIn("No hay documentos rag", result)


if __name__ == "__main__":
    unittest.main()
