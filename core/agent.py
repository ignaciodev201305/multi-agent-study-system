"""Construcción del supervisor y los subagentes que atienden una sesión."""

from langchain.agents import create_agent
from langgraph_supervisor.supervisor import create_supervisor

from config.settings import (
    FORMAT_RULES,
    SUBAGENT_MODEL,
    SUBAGENT_TEMPERATURE,
    SUPERVISOR_MODEL,
    SUPERVISOR_TEMPERATURE,
)
from core.llm import BuildModel
from core.prompts import DOCUMENT_TOOL_RULES
from core.tools import ToolsBuilder

class MultiAgentSystemBuilder:
    """Instancia el supervisor y sus subagentes a partir del contexto construido."""
    def __init__(self, supervisor_context, subagents_context):
        self.supervisor = supervisor_context
        self.subagents = subagents_context
        self.model_builder = BuildModel()
        self.tools_builder = ToolsBuilder()

    def build_subagents(self, session_id):
        """Crea un agente por subagente con acceso a los PDFs RAG de la sesión."""
        subagents = []
        subagent_model = self.model_builder.build_model(model=SUBAGENT_MODEL, temperature=SUBAGENT_TEMPERATURE)
        for subagent in self.subagents:
            subagents.append(
                    create_agent(subagent_model, system_prompt=subagent.prompt, name=subagent.specialization.replace(" ","_"), tools=self.tools_builder.get_vector_store_tools(session_id))
                )
        return subagents

    def build_supervisor(self, session_id):
        """Crea el supervisor que coordina los subagentes y consulta los documentos."""
        supervisor_model = self.model_builder.build_model(model=SUPERVISOR_MODEL, temperature=SUPERVISOR_TEMPERATURE)
        prompt = f"{self.supervisor.prompt} \n {FORMAT_RULES} \n {DOCUMENT_TOOL_RULES}"
        return create_supervisor(
                    agents=self.build_subagents(session_id), model=supervisor_model, prompt=prompt, supervisor_name=self.supervisor.name.replace(" ", "_"), tools=self.tools_builder.get_vector_store_tools(session_id)
                ).compile()
