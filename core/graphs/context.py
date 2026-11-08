"""Grafo de contextualización: clasifica lo que pide el usuario y genera el plan de estudio."""

import logging

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from core.chain import ChainBuilder
from core.llm import build_model, model_for_turn, turn_models
from core.agents.catalog import catalog_for_factory
from core.prompts.context import CONTEXT_CLASSIFIER_PROMPT, STUDY_PLAN_JSON_RULES, STUDY_PLAN_PROMPT
from core.schemas import ContextClassification, StudyPlan
from core.states import ContextStudyState
from core.tools import build_tools

logger = logging.getLogger(__name__)


class ContextStudyGraph:
    """Grafo que clasifica el contexto del usuario y construye el sistema multiagente."""
    def __init__(self):
        self.model = build_model("context_classifier")
        self.factory_model = build_model("factory")
        self.chain_builder = ChainBuilder()
    def build_subgraph(self):
        graph = StateGraph(ContextStudyState)

        async def context_classifier(state, config):
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
            model = model_for_turn(config, "context_classifier", self.model)
            # Se crea en cada ronda: Tavily valida la API key al instanciarse y el arranque no debe depender de ella.
            agent = create_agent(model, tools=build_tools(["web_search"]),system_prompt=CONTEXT_CLASSIFIER_PROMPT.format(reaming_rounds=reaming_rounds), response_format=ToolStrategy(ContextClassification))

            response = await agent.ainvoke({"messages": messages})

            return {"classified_context": response["structured_response"], "clarification_round": clarification_round+1}

        def verify_approval_context(state):
            """Deriva a la creación del plan o a la espera de respuestas."""
            if state['classified_context'].approved_context:
                return "build_study_plan"

            return "wait_user_response"

        async def build_study_plan(state, config):
            """Elige arquetipos del catálogo y escribe el temario de la sesión."""
            classified_context = state['classified_context']

            session_objetive = f"Objetivo:  {classified_context.session_objetive}"
            session_context = f"Contexto de sesion: {classified_context.session_context}"
            previous_level = f"Nivel previo del usuario: {classified_context.previous_level}"

            context = "\n".join([session_objetive, session_context, previous_level])

            model = model_for_turn(config, "factory", self.factory_model)
            resolved = (turn_models(config) or {}).get("factory") or {}
            chain = self.chain_builder.build_structured_chain(
                model,
                STUDY_PLAN_PROMPT,
                StudyPlan,
                STUDY_PLAN_PROMPT + STUDY_PLAN_JSON_RULES,
                # Una capacidad desconocida se intenta igual; el parser cubre el fallo.
                native=resolved.get("supports_structured_output") is not False,
            )

            response = await chain.ainvoke({"context": context, "catalog": catalog_for_factory()})
            return {"study_plan": response}


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
        graph.add_node("build_study_plan", build_study_plan)
        graph.add_node("wait_user_response", wait_user_response)

        graph.add_edge(START, "context_classifier")
        graph.add_conditional_edges(
            "context_classifier", verify_approval_context,
            {
                "build_study_plan": "build_study_plan",
                "wait_user_response": "wait_user_response"
            })
        graph.add_edge("wait_user_response", "context_classifier")
        graph.add_edge("build_study_plan", END)

        return graph.compile()
