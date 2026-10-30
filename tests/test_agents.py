"""Pruebas del catálogo, el plan de estudio y el supervisor con subagentes como tools."""

import unittest
from unittest.mock import patch

from langchain.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from pydantic import ValidationError

from core.agents.builder import SupervisorBuilder
from core.agents.catalog import ARCHETYPES, catalog_for_factory
from core.schemas import StudyPlan
from core.tools import TurnContext
from core.tools.documents import get_study_material, search_documents


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
                "args": {"topic": "Definición", "status": "dominado", "evidence": "Respondió bien."},
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

        context = TurnContext(session_id="s", documents=[STUDY_MAP])
        result = await supervisor.ainvoke(
            {"messages": [SystemMessage(content="contexto"), HumanMessage(content="hola")]},
            context=context,
        )

        tool_messages = [message for message in result["messages"] if message.type == "tool"]
        self.assertIn("regla de la cadena", tool_messages[0].content)
        self.assertEqual(context.progress_updates["Definición"]["status"], "dominado")
        self.assertEqual(result["messages"][-1].content, "Aquí va la explicación del especialista.")

    def test_system_prompt_includes_plan(self):
        prompt = SupervisorBuilder(_plan()).system_prompt()
        self.assertIn("1. Definición", prompt)
        self.assertIn("2. Regla de la cadena", prompt)
        self.assertIn("- evaluador:", prompt)
        self.assertIn("$...$", prompt)


def _tools_without_web(names):
    from core.tools import build_tools
    return build_tools([name for name in names if name != "web_search"])


class _FakeStore:
    def similarity_search(self, query, k, filter):
        return [Document(page_content="La derivada mide el cambio.", metadata={"document_id": "r1", "pages": "12,13"})]


class _Runtime:
    def __init__(self, context):
        self.context = context


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

    async def test_search_without_rag_documents_does_not_touch_store(self):
        with patch("core.tools.documents._get_vector_store") as store:
            result = await search_documents.coroutine(
                query="x", runtime=_Runtime(TurnContext("s", [STUDY_MAP]))
            )
        store.assert_not_called()
        self.assertIn("No hay documentos rag", result)


if __name__ == "__main__":
    unittest.main()
