"""Pruebas del grafo padre sobre SQLite real: contexto con interrupt y turno de estudio."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain.messages import AIMessage, HumanMessage
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langgraph.types import Command

from core.graphs import StudyGraph, open_checkpointer
from core.schemas import ContextClassification, StudyPlan
from core.streaming import execute_astream_graph

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
            patch("core.graphs.context.ToolsBuilder"),
        ):
            self.study_graph = StudyGraph()
        self.study_graph.context.chain_builder = SimpleNamespace(
            build_chain=lambda *_: SimpleNamespace(ainvoke=AsyncMock(return_value=PLAN))
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
                "args": {"topic": "Definición", "status": "en_progreso", "evidence": "Saludó."},
                "id": "call-1",
            }]),
            AIMessage(content="Empecemos."),
        ]))
        subagent_model = _ToolModel(messages=iter([]))
        models = {"supervisor": supervisor_model, "subagent_light": subagent_model}
        with patch("core.agents.builder.build_model", side_effect=lambda role, **_: models[role]):
            self.study_graph.session._get_or_build_supervisor("t", PLAN)

        state, _ = await self.run_turn({"messages": [HumanMessage(content="hola")], "documents": []})

        self.assertEqual(state["messages"][-1].content, "Empecemos.")
        self.assertEqual([message.content for message in state["messages"]], ["hola", "Empecemos."])
        stored = (await self.graph.aget_state(self.config)).values
        self.assertEqual(stored["progress"]["Definición"]["status"], "en_progreso")
        thinking = [event for event in self.sender.events if event["type"] == "thinking"]
        self.assertIn("Empecemos.", "".join(event["content"] for event in thinking))

    async def test_delete_thread_clears_state(self):
        await self.run_context()
        await self.graph.checkpointer.adelete_thread("t")
        self.assertEqual((await self.graph.aget_state(self.config)).values, {})


if __name__ == "__main__":
    unittest.main()
