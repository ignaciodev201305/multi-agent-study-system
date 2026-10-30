"""Grafo de contextualización: clasifica lo que pide el usuario y genera el sistema multiagente."""

import logging

import aiosqlite

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from core.chain import ChainBuilder
from core.llm import build_model
from core.prompts.context import CONTEXT_CLASSIFIER_PROMPT, FACTORY_MULTI_AGENT_SYSTEM_PROMPT
from core.schemas import ContextClassification, MultiAgentSystem
from core.states import ContextStudyState
from core.tools import ToolsBuilder

logger = logging.getLogger(__name__)


class ContextStudyGraph:
    """Grafo que clasifica el contexto del usuario y construye el sistema multiagente."""
    def __init__(self):
        self.model = build_model("context_classifier")
        self.factory_model = build_model("factory")
        self.chain_builder = ChainBuilder()
        self.tools_builder = ToolsBuilder()
    def build_graph(self):
        graph = StateGraph(ContextStudyState)

        async def context_classifier(state):
            """Clasifica el contexto acumulado y decide si ya es suficiente."""
            messages = state['messages']
            current_classified_context = state.get("classified_context", None)
            clarification_round = state.get("clarification_round", 0)

            # Se fuerza la aprobación del contexto al alcanzar el límite de rondas
            if clarification_round==3:
                current_classified_context.approved_context = True
                current_classified_context.questions = None
                return {"classified_context": current_classified_context}

            reaming_rounds = 3 - clarification_round
            logger.debug("Rondas de aclaración restantes: %s", reaming_rounds)
            agent = create_agent(self.model, tools=self.tools_builder.get_tavily_tools(),system_prompt=CONTEXT_CLASSIFIER_PROMPT.format(reaming_rounds=reaming_rounds), response_format=ToolStrategy(ContextClassification))

            response = await agent.ainvoke({"messages": messages})

            return {"classified_context": response["structured_response"], "clarification_round": clarification_round+1}

        def verify_approval_context(state):
            """Deriva a la creación del sistema o a la espera de respuestas."""
            if state['classified_context'].approved_context:
                return "factory_multi_agent_system"

            return "wait_user_response"

        async def factory_multi_agent_system(state):
            """Genera la configuración del supervisor y sus subagentes."""
            classified_context = state['classified_context']

            session_objetive = f"Objetivo:  {classified_context.session_objetive}"
            session_context = f"Contexto de sesion: {classified_context.session_context}"
            previous_level = f"Nivel previo del usuario: {classified_context.previous_level}"

            context = "\n".join([session_objetive, session_context, previous_level])

            chain = self.chain_builder.build_chain(self.factory_model, FACTORY_MULTI_AGENT_SYSTEM_PROMPT, MultiAgentSystem)

            response = await chain.ainvoke({"context": context})
            return {"multi_agent_system": response}


        def wait_user_response(state):
            """Suspende el grafo para pedir al usuario las preguntas de aclaración."""
            classified_context = state["classified_context"]
            user_responses = None
            if classified_context.questions:
                user_responses = interrupt("\n".join([question for question in classified_context.questions]))
            else:
                classified_context.approved_context = True # Fuerza la aprobación cuando no hay preguntas pendientes
            return {
                "messages": [HumanMessage(content=user_responses)] if user_responses else [SystemMessage(content="Se forzo la aprobación.")],
                "classified_context": classified_context
            }

        graph.add_node("context_classifier", context_classifier)
        graph.add_node("factory_multi_agent_system", factory_multi_agent_system)
        graph.add_node("wait_user_response", wait_user_response)

        graph.add_edge(START, "context_classifier")
        graph.add_conditional_edges(
            "context_classifier", verify_approval_context,
            {
                "factory_multi_agent_system": "factory_multi_agent_system",
                "wait_user_response": "wait_user_response"
            })
        graph.add_edge("wait_user_response", "context_classifier")
        graph.add_edge("factory_multi_agent_system", END)

        self.graph = graph


    async def compile_graph(self, checkpointer=None):
        self.build_graph()

        if checkpointer is None:
            serde = JsonPlusSerializer(allowed_msgpack_modules=[("core.schemas", "ContextClassification"), ("core.schemas", "MultiAgentSystem")])
            conn = await aiosqlite.connect("data/context_study.db", check_same_thread=True)
            checkpointer = AsyncSqliteSaver(conn, serde=serde)
        return self.graph.compile(
            checkpointer=checkpointer
        )
