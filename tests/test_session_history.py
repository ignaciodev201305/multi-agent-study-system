"""Pruebas del historial de sesión: sin duplicados y nada se descarta sin resumir."""

import unittest
from unittest.mock import patch

from langchain.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from config.settings import SESSION_HISTORY_KEEP_MESSAGES, SESSION_HISTORY_MAX_MESSAGES
from core.graphs import SessionStudyGraph
from core.streaming import token_text


class _FakeChain:
    """Resumidor simulado: registra lo que recibe y devuelve un resumen acumulado."""

    def __init__(self):
        self.inputs = []

    async def ainvoke(self, values):
        self.inputs.append(values)
        return AIMessage(content=f"{values['current_summary']}|{values['messages_to_summarize']}")


class _FakeSupervisor:
    def __init__(self):
        self.received = []

    async def ainvoke(self, values, config=None, context=None):
        self.received.append(values["messages"])
        last = values["messages"][-1].content
        return {"messages": values["messages"] + [AIMessage(content=f"respuesta a {last}")]}


class SessionHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch("core.graphs.session.build_model"), patch("core.graphs.session.ChainBuilder"):
            self.session_graph = SessionStudyGraph()
        self.chain = _FakeChain()
        self.session_graph.chain_builder.build_chain.return_value = self.chain
        self.supervisor = _FakeSupervisor()
        self.session_graph._get_or_build_supervisor = lambda *_: self.supervisor
        self.session_graph._get_cached_supervisor = lambda *_: self.supervisor
        self.graph = await self.session_graph.compile_graph(checkpointer=InMemorySaver())
        self.config = {"configurable": {"thread_id": "t"}}

    async def run_turns(self, count):
        for index in range(count):
            await self.graph.ainvoke(
                {
                    "messages": [HumanMessage(content=f"m{index}")],
                    "study_plan": None,
                    "documents": [{
                        "document_id": "d1",
                        "document_type": "study_map",
                        "original_filename": "guia.pdf",
                        "summary": {"title": "Guía", "summary": "Derivadas."},
                    }],
                },
                self.config,
            )
        return (await self.graph.aget_state(self.config)).values

    async def test_history_has_no_duplicates_and_is_bounded(self):
        state = await self.run_turns(12)
        contents = [message.content for message in state["messages"]]
        self.assertEqual(len(contents), len(set(contents)))
        self.assertLessEqual(len(contents), SESSION_HISTORY_MAX_MESSAGES + 2)
        self.assertFalse(any(isinstance(message, SystemMessage) for message in state["messages"]))

    async def test_every_removed_message_was_summarized(self):
        state = await self.run_turns(12)
        kept = {message.content for message in state["messages"]}
        summarized = "\n".join(values["messages_to_summarize"] for values in self.chain.inputs)
        for index in range(12):
            for content in (f"m{index}", f"respuesta a m{index}"):
                self.assertTrue(content in kept or content in summarized, content)
        self.assertTrue(state["summary"])

    async def test_supervisor_receives_composed_context_first(self):
        await self.run_turns(SESSION_HISTORY_MAX_MESSAGES)
        messages = self.supervisor.received[-1]
        self.assertIsInstance(messages[0], SystemMessage)
        self.assertIn("<documentos>", messages[0].content)
        self.assertIn("RESUMEN DE LA SESION", messages[0].content)
        self.assertEqual(sum(isinstance(message, SystemMessage) for message in messages), 1)
        self.assertGreaterEqual(len(messages) - 1, SESSION_HISTORY_KEEP_MESSAGES)


class TokenTextTests(unittest.TestCase):
    def test_string_content_is_sent_as_is(self):
        self.assertEqual(token_text("x²"), "x²")

    def test_block_content_joins_text(self):
        self.assertEqual(token_text([{"type": "text", "text": "a"}, "b", {"type": "image"}]), "ab")


if __name__ == "__main__":
    unittest.main()
