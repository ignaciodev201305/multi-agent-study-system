"""Construcción del supervisor y los subagentes que atienden una sesión."""

from langchain.agents import create_agent
from langgraph_supervisor.supervisor import create_supervisor

from core.llm import build_model
from core.prompts.session import DOCUMENT_TOOL_RULES, MATH_FORMAT_RULE
from core.tools import ToolsBuilder

class MultiAgentSystemBuilder:
    """Instancia el supervisor y sus subagentes a partir del contexto construido."""
    def __init__(self, supervisor_context, subagents_context):
        self.supervisor = supervisor_context
        self.subagents = subagents_context
        self.tools_builder = ToolsBuilder()

    def build_subagents(self, session_id):
        """Crea un agente por subagente con acceso a los PDFs RAG de la sesión."""
        subagents = []
        subagent_model = build_model("subagent_light")
        for subagent in self.subagents:
            subagents.append(
                    create_agent(subagent_model, system_prompt=subagent.prompt, name=subagent.specialization.replace(" ","_"), tools=self.tools_builder.get_vector_store_tools(session_id))
                )
        return subagents

    def build_supervisor(self, session_id):
        """Crea el supervisor que coordina los subagentes y consulta los documentos."""
        supervisor_model = build_model("supervisor")
        prompt = f"{self.supervisor.prompt}\n\n{MATH_FORMAT_RULE}\n{DOCUMENT_TOOL_RULES}"
        return create_supervisor(
                    agents=self.build_subagents(session_id), model=supervisor_model, prompt=prompt, supervisor_name=self.supervisor.name.replace(" ", "_"), tools=self.tools_builder.get_vector_store_tools(session_id)
                ).compile()
