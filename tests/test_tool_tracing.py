"""Pruebas del registro de tools por turno y de la medición del relevo del supervisor."""

import tempfile
import unittest
from pathlib import Path

from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from core.usage import RELAY_MIN_BLOCK_CHARS, ToolRecord, UsageRepository, UsageTracker, relayed_chars

SPECIALIST_ANSWER = "La derivada de x^3 es 3x^2 porque aplicamos la regla de la potencia: bajamos el exponente."


class _ToolModel(GenericFakeChatModel):
    """Modelo simulado que acepta bind_tools y responde con mensajes guionados."""

    def bind_tools(self, tools, **kwargs):
        return self


def _call(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


@tool
def ping() -> str:
    """Tool de prueba que responde bien."""
    return "pong"


@tool
def broken() -> str:
    """Tool de prueba que informa un error como texto."""
    return "Error: el tema 9 no existe."


def _supervisor():
    """Supervisor con un especialista anidado que usa sus propias tools."""
    specialist = create_agent(
        _ToolModel(messages=iter([_call("ping", {}, "s1"), AIMessage(content=SPECIALIST_ANSWER)])),
        tools=[ping],
        name="explicador",
    )

    @tool
    async def explicador(request: str) -> str:
        """Especialista de prueba."""
        result = await specialist.ainvoke({"messages": [HumanMessage(content=request)]})
        return result["messages"][-1].content

    return create_agent(
        _ToolModel(messages=iter([
            _call("explicador", {"request": "derivadas"}, "p1"),
            _call("broken", {}, "p2"),
            AIMessage(content=f"Mira: {SPECIALIST_ANSWER} ¿Seguimos?"),
        ])),
        tools=[explicador, broken],
        name="profesor_principal",
    )


class ToolTrackingTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_agent_tool_and_errors_including_nested_specialists(self):
        tracker = UsageTracker(relay_tools=frozenset({"explicador"}))
        # El modelo simulado no informa tokens: el tracker lo avisa en el log.
        with self.assertLogs("core.usage", level="WARNING"):
            await _supervisor().ainvoke({"messages": [HumanMessage(content="hola")]}, config={"callbacks": [tracker]})

        self.assertEqual(
            [(record.agent, record.tool, record.ok) for record in tracker.tool_records],
            [("explicador", "ping", True), ("profesor_principal", "explicador", True), ("profesor_principal", "broken", False)],
        )
        self.assertEqual(tracker.relay_sources, [SPECIALIST_ANSWER])

    async def test_tool_exception_is_recorded_as_failure(self):
        @tool
        def explode() -> str:
            """Tool que lanza una excepción."""
            raise RuntimeError("falló")

        tracker = UsageTracker()
        with self.assertRaises(RuntimeError):
            await explode.ainvoke({}, config={"callbacks": [tracker]})
        self.assertEqual(tracker.tool_records, [ToolRecord("desconocido", "explode", False, 0)])


class RelayTests(unittest.TestCase):
    def test_counts_only_long_copied_blocks_once(self):
        answer = f"Mira: {SPECIALIST_ANSWER} ¿Seguimos?"
        self.assertEqual(relayed_chars([SPECIALIST_ANSWER], answer), len(SPECIALIST_ANSWER))
        # La misma fuente dos veces no duplica la cuenta.
        self.assertEqual(relayed_chars([SPECIALIST_ANSWER, SPECIALIST_ANSWER], answer), len(SPECIALIST_ANSWER))

    def test_short_coincidences_do_not_count(self):
        short = "x" * (RELAY_MIN_BLOCK_CHARS - 1)
        self.assertEqual(relayed_chars([short], f"Respuesta con {short} adentro."), 0)
        self.assertEqual(relayed_chars(["Texto del especialista."], "Otra cosa distinta."), 0)


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = await UsageRepository.open(Path(self.directory.name) / "app.db")

    async def asyncTearDown(self):
        await self.repository.close()
        self.directory.cleanup()

    async def rows(self, query):
        cursor = await self.repository._connection.execute(query)
        return [tuple(row) for row in await cursor.fetchall()]

    async def test_saves_tool_records_and_relay(self):
        await self.repository.add_tool_records("c", "t", [ToolRecord("explicador", "math", False, 12)])
        await self.repository.add_relay("c", "t", [SPECIALIST_ANSWER], f"Mira: {SPECIALIST_ANSWER}")
        await self.repository.add_relay("c", "t2", [], "Sin especialistas.")

        self.assertEqual(
            await self.rows("SELECT turn_id, agent, tool, ok, output_chars FROM tool_usage"),
            [("t", "explicador", "math", 0, 12)],
        )
        self.assertEqual(
            await self.rows("SELECT turn_id, specialist_chars, relayed_chars FROM turn_relay"),
            [("t", len(SPECIALIST_ANSWER), len(SPECIALIST_ANSWER))],
        )

    async def test_delete_chat_removes_tools_and_relay_of_that_chat(self):
        for chat_id in ("c", "otra"):
            await self.repository.add_tool_records(chat_id, "t", [ToolRecord("explicador", "math", True, 3)])
            await self.repository.add_relay(chat_id, "t", [SPECIALIST_ANSWER], SPECIALIST_ANSWER)

        await self.repository.delete_chat("c")

        self.assertEqual(await self.rows("SELECT chat_id FROM tool_usage"), [("otra",)])
        self.assertEqual(await self.rows("SELECT chat_id FROM turn_relay"), [("otra",)])


if __name__ == "__main__":
    unittest.main()
