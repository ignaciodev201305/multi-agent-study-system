"""Pruebas del middleware de los agentes: tope de llamadas por turno y caché de prompts."""

import unittest

from langchain.agents import create_agent
from langchain.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain.tools import tool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from core.agents.builder import SupervisorBuilder
from core.agents.middleware import (
    CACHE_CONTROL,
    CallLimitMiddleware,
    PromptCacheMiddleware,
    calls_this_turn,
)
from core.schemas import StudyPlan


@tool
def ping() -> str:
    """Tool de prueba."""
    return "pong"


class _LoopingModel(BaseChatModel):
    """Pide una tool en cada llamada salvo que tool_choice sea none; registra lo que recibe."""
    model_name: str = "fake/loop"
    ignore_none: bool = False
    tool_choices: list = Field(default_factory=list)
    received: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "loop"

    def bind_tools(self, tools, tool_choice=None, **kwargs):
        self.tool_choices.append(tool_choice)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(messages)
        if self.tool_choices and self.tool_choices[-1] == "none" and not self.ignore_none:
            message = AIMessage(content="Respuesta final.")
        else:
            message = AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": f"c{len(self.received)}"}])
        return ChatResult(generations=[ChatGeneration(message=message)])


def _agent(model, *middleware, system_prompt="Prompt fijo."):
    return create_agent(model, tools=[ping], system_prompt=system_prompt, middleware=list(middleware))


class CallsThisTurnTests(unittest.TestCase):
    def test_counts_model_answers_after_the_last_human_message(self):
        messages = [
            SystemMessage(content="contexto"),
            HumanMessage(content="antes"),
            AIMessage(content="respuesta vieja"),
            HumanMessage(content="ahora"),
            AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": "a"}]),
            ToolMessage(content="pong", tool_call_id="a"),
            AIMessage(content="", tool_calls=[{"name": "ping", "args": {}, "id": "b"}]),
        ]
        self.assertEqual(calls_this_turn(messages), 2)
        self.assertEqual(calls_this_turn(messages[:4]), 0)


class CallLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_call_goes_without_tools_and_closes_the_turn(self):
        model = _LoopingModel()
        agent = _agent(model, CallLimitMiddleware(3, "Límite.", "prueba"))
        with self.assertLogs("core.agents.middleware", level="WARNING"):
            result = await agent.ainvoke({"messages": [HumanMessage(content="hola")]})

        self.assertEqual(len(model.received), 3)
        self.assertEqual(model.tool_choices, [None, None, "none"])
        self.assertEqual(result["messages"][-1].content, "Respuesta final.")

    async def test_hard_stop_if_the_provider_ignores_tool_choice(self):
        model = _LoopingModel(ignore_none=True)
        agent = _agent(model, CallLimitMiddleware(3, "Límite.", "prueba"))
        with self.assertLogs("core.agents.middleware", level="WARNING"):
            result = await agent.ainvoke({"messages": [HumanMessage(content="hola")]})

        self.assertEqual(len(model.received), 3)
        self.assertEqual(result["messages"][-1].content, "Límite.")

    async def test_turns_under_the_limit_are_untouched(self):
        class _OneToolModel(_LoopingModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                if any(isinstance(message, ToolMessage) for message in messages):
                    self.received.append(messages)
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Listo."))])
                return super()._generate(messages, stop, run_manager, **kwargs)

        model = _OneToolModel()
        result = await _agent(model, CallLimitMiddleware(3, "Límite.", "prueba")).ainvoke(
            {"messages": [HumanMessage(content="hola")]}
        )
        self.assertEqual(model.tool_choices, [None, None])
        self.assertEqual(result["messages"][-1].content, "Listo.")


class PromptCacheTests(unittest.IsolatedAsyncioTestCase):
    async def first_call(self, model_name):
        model = _LoopingModel(model_name=model_name)
        # Con tope 1 la primera llamada ya es la última y el agente termina sin usar la tool.
        agent = _agent(model, CallLimitMiddleware(1, "Límite.", "prueba"), PromptCacheMiddleware())
        with self.assertLogs("core.agents.middleware", level="WARNING"):
            await agent.ainvoke({"messages": [SystemMessage(content="Contexto del turno."), HumanMessage(content="hola")]})
        return model.received[0]

    async def test_anthropic_models_get_both_cache_points(self):
        system, context, human = await self.first_call("anthropic/claude-haiku-5.5")
        self.assertEqual(system.content, [{"type": "text", "text": "Prompt fijo.", "cache_control": CACHE_CONTROL}])
        self.assertEqual(context.content[-1]["cache_control"], CACHE_CONTROL)
        self.assertEqual(context.content[-1]["text"], "Contexto del turno.")
        self.assertEqual(human.content, "hola")

    async def test_other_models_are_untouched(self):
        system, context, _ = await self.first_call("deepseek/deepseek-v4.1-flash")
        self.assertEqual(system.content, "Prompt fijo.")
        self.assertEqual(context.content, "Contexto del turno.")


class SupervisorWiringTests(unittest.TestCase):
    def test_supervisor_has_both_middleware(self):
        from unittest.mock import patch

        plan = StudyPlan.model_validate({
            "subject": "derivadas",
            "syllabus": ["Definición"],
            "supervisor_focus": "Principiante.",
            "archetypes": [{"archetype": "explicador", "focus": "Derivadas."}],
        })
        with (
            patch("core.agents.builder.build_model"),
            patch("core.agents.builder.build_tools", return_value=[]),
            patch("core.agents.builder.create_agent") as create_agent_mock,
        ):
            SupervisorBuilder(plan).build()
        kinds = [
            [type(item) for item in call.kwargs["middleware"]] for call in create_agent_mock.call_args_list
        ]
        # Un especialista y el supervisor.
        self.assertEqual(kinds, [[CallLimitMiddleware, PromptCacheMiddleware]] * 2)


if __name__ == "__main__":
    unittest.main()
