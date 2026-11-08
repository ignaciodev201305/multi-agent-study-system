"""Pruebas del grafo padre sobre SQLite real: contexto con interrupt y turno de estudio."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain.messages import AIMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.types import Command

from core.agents.builder import SupervisorBuilder
from core.graphs import StudyGraph, compose_session_context, open_checkpointer
from core.schemas import ContextClassification, StudyPlan
from core.streaming import execute_astream_graph

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
PLAN = StudyPlan.model_validate({
    "subject": "derivadas",
    "syllabus": ["Definición"],
    "supervisor_focus": "Principiante.",
    "archetypes": [{"archetype": "explicador", "focus": "Derivadas."}],
})
PENDING = ContextClassification(approved_context=False, questions=["¿Qué nivel tienes?"])
APPROVED = ContextClassification(
    approved_context=True,
    questions=None,
    session_objetive="Parcial",
    session_context="Derivadas",
    previous_level="principiante",
    title_session="Derivadas",
)


def _entry(number, due_at):
    return {
        "topic_number": number,
        "topic": f"Tema {number}",
        "status": "en_progreso",
        "box": 0,
        "due_at": due_at,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "reviews": [{"status": "en_progreso", "evidence": "Bien.", "created_at": "2026-01-01T00:00:00+00:00"}],
    }


class _ToolModel(GenericFakeChatModel):
    # El modelo simulado no puede hacer stream de un mensaje vacío con tool_calls.
    disable_streaming: bool = True

    def bind_tools(self, tools, **kwargs):
        return self


class _Sender:
    def __init__(self):
        self.events = []

    async def send_json(self, payload):
        self.events.append(payload)


class StudyGraphTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        with (
            patch("core.graphs.session.build_model"),
            patch("core.graphs.context.build_model"),
            patch("core.graphs.context.build_tools", return_value=[]),
        ):
            self.study_graph = StudyGraph()
        self.study_graph.context.chain_builder = SimpleNamespace(
            build_structured_chain=lambda *_, **__: SimpleNamespace(ainvoke=AsyncMock(return_value=PLAN))
        )
        checkpointer = await open_checkpointer(Path(self.directory.name) / "study.db")
        self.graph = await self.study_graph.compile_graph(checkpointer=checkpointer)
        self.config = {"configurable": {"thread_id": "t"}}
        self.sender = _Sender()

    async def asyncTearDown(self):
        await self.graph.checkpointer.conn.close()
        self.directory.cleanup()

    def run_turn(self, graph_input):
        return execute_astream_graph(self.graph, self.sender, graph_input, self.config)

    async def run_context(self):
        """Primera ronda pide aclaración; la segunda aprueba y crea el plan."""
        agent = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
            {"structured_response": PENDING},
            {"structured_response": APPROVED},
        ]))
        with patch("core.graphs.context.create_agent", return_value=agent):
            _, interrupt_info = await self.run_turn({"messages": [HumanMessage(content="quiero estudiar")]})
            snapshot = await self.graph.aget_state(self.config)
            self.assertEqual(interrupt_info[0].value, "¿Qué nivel tienes?")
            self.assertEqual(snapshot.interrupts[0].value, "¿Qué nivel tienes?")
            return await self.run_turn(Command(resume="desde cero"))

    async def test_context_interrupt_resumes_and_creates_plan(self):
        state, interrupt_info = await self.run_context()
        self.assertIsNone(interrupt_info)
        self.assertIsInstance(state["study_plan"], StudyPlan)
        self.assertEqual(state["classified_context"].title_session, "Derivadas")
        self.assertEqual(state["messages"], [])

    async def test_study_turn_uses_real_supervisor_and_persists_progress(self):
        await self.run_context()
        supervisor_model = _ToolModel(messages=iter([
            AIMessage(content="", tool_calls=[{
                "name": "record_progress",
                "args": {"topic_number": 1, "status": "en_progreso", "evidence": "Saludó."},
                "id": "call-1",
            }]),
            AIMessage(content="Empecemos."),
        ]))
        subagent_model = _ToolModel(messages=iter([]))
        models = {"supervisor": supervisor_model, "subagent_light": subagent_model}
        with patch("core.agents.builder.build_model", side_effect=lambda role, **_: models[role]):
            self.study_graph.session._get_or_build_supervisor("t", PLAN)

        progress = [_entry(1, due_at="2026-01-01T00:00:00+00:00")]
        state, _ = await self.run_turn({
            "messages": [HumanMessage(content="hola")],
            "documents": [],
            "progress": progress,
        })

        self.assertEqual(state["messages"][-1].content, "Empecemos.")
        self.assertEqual([message.content for message in state["messages"]], ["hola", "Empecemos."])
        self.assertEqual(state["progress_updates"], [{
            "topic_number": 1,
            "topic": "Definición",
            "status": "en_progreso",
            "evidence": "Saludó.",
        }])
        stored = (await self.graph.aget_state(self.config)).values
        self.assertEqual(stored["progress"], progress)
        self.assertEqual(stored["progress_updates"], state["progress_updates"])
        thinking = [event for event in self.sender.events if event["type"] == "thinking"]
        self.assertIn("Empecemos.", "".join(event["content"] for event in thinking))

    async def test_session_from_previous_format_keeps_working(self):
        await self.run_context()
        await self.graph.aupdate_state(self.config, {
            "progress": {"Definición": {"status": "dominado", "evidence": "Antes."}},
        })
        supervisor_model = _ToolModel(messages=iter([AIMessage(content="Seguimos.")]))
        models = {"supervisor": supervisor_model, "subagent_light": _ToolModel(messages=iter([]))}
        with patch("core.agents.builder.build_model", side_effect=lambda role, **_: models[role]):
            self.study_graph.session._get_or_build_supervisor("t", PLAN)

        state, _ = await self.run_turn({
            "messages": [HumanMessage(content="hola")],
            "documents": [],
            "progress": [],
        })

        self.assertEqual(state["messages"][-1].content, "Seguimos.")
        self.assertEqual(state["progress"], [])

    async def test_delete_thread_clears_state(self):
        await self.run_context()
        await self.graph.checkpointer.adelete_thread("t")
        self.assertEqual((await self.graph.aget_state(self.config)).values, {})


class ReviewContextTests(unittest.TestCase):
    def content(self, progress):
        return compose_session_context({"progress": progress}, NOW).content

    def test_due_topic_appears_in_its_own_section(self):
        content = self.content([
            _entry(1, due_at="2026-01-09T12:00:00+00:00"),
            _entry(2, due_at="2026-01-12T12:00:00+00:00"),
        ])
        progress_section, due_section = content.split("REPASOS PENDIENTES:\n")
        self.assertIn("- 1. Tema 1: en_progreso (Bien.)", progress_section)
        self.assertIn("- 2. Tema 2: en_progreso (Bien.)", progress_section)
        self.assertIn("- 1. Tema 1", due_section)
        self.assertNotIn("Tema 2", due_section)

    def test_no_due_topics_means_no_section(self):
        content = self.content([_entry(1, due_at="2026-01-12T12:00:00+00:00")])
        self.assertIn("PROGRESO DEL ALUMNO", content)
        self.assertNotIn("REPASOS PENDIENTES", content)

    def test_due_section_keeps_five_oldest(self):
        content = self.content([
            _entry(number, due_at=f"2026-01-0{9 - number}T12:00:00+00:00") for number in range(1, 8)
        ])
        due_section = content.split("REPASOS PENDIENTES:\n")[1]
        self.assertEqual(
            due_section.splitlines(),
            [f"- {number}. Tema {number}" for number in (7, 6, 5, 4, 3)],
        )

    def test_topic_without_reviews_has_no_evidence(self):
        entry = {**_entry(1, due_at="2026-01-12T12:00:00+00:00"), "reviews": []}
        self.assertIn("- 1. Tema 1: en_progreso\n", f"{self.content([entry])}\n")

    def test_prompt_uses_topic_numbers_and_reviews(self):
        prompt = SupervisorBuilder(PLAN).system_prompt()
        self.assertIn("topic_number", prompt)
        self.assertIn("REPASOS PENDIENTES", prompt)


if __name__ == "__main__":
    unittest.main()
