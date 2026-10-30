"""Construcción del supervisor de una sesión: los subagentes se le exponen como tools."""

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain.tools import ToolRuntime, tool

from core.agents.catalog import ARCHETYPES, Archetype, archetype_prompt
from core.llm import build_model
from core.prompts.session import DOCUMENT_TOOL_RULES, MATH_FORMAT_RULE, SUPERVISOR_PROMPT
from core.schemas import StudyPlan
from core.streaming import token_text
from core.tools import TurnContext, build_tools

SUPERVISOR_TOOLS = ("search_documents", "get_study_material", "record_progress")


def _subagent_tool(archetype: Archetype, focus: str):
    """Envuelve un subagente como tool: recibe un pedido autocontenido y devuelve su respuesta."""
    model = build_model(f"subagent_{archetype.tier}", temperature=archetype.temperature)
    subagent = create_agent(
        model,
        tools=build_tools(archetype.tools),
        system_prompt=f"{archetype_prompt(archetype, focus)}\n\n{MATH_FORMAT_RULE}",
        context_schema=TurnContext,
        name=archetype.name,
    )

    async def consult(request: str, runtime: ToolRuntime[TurnContext]) -> str:
        result = await subagent.ainvoke(
            {"messages": [HumanMessage(content=request)]},
            context=runtime.context,
        )
        return token_text(result["messages"][-1].content)

    consult.__doc__ = (
        f"{archetype.description} Foco: {focus}\n"
        "request: pedido autocontenido (el especialista no ve la conversación): tema exacto, "
        "nivel del alumno, lo que dijo o respondió y qué necesitas que prepare."
    )
    return tool(archetype.name)(consult)


class SupervisorBuilder:
    """Instancia el supervisor de la sesión a partir de su plan de estudio."""

    def __init__(self, study_plan: StudyPlan):
        self.study_plan = study_plan

    def build(self):
        specialists = [
            _subagent_tool(ARCHETYPES[selected.archetype], selected.focus)
            for selected in self.study_plan.archetypes
        ]
        return create_agent(
            build_model("supervisor"),
            tools=specialists + build_tools(SUPERVISOR_TOOLS),
            system_prompt=self.system_prompt(),
            context_schema=TurnContext,
            name="profesor_principal",
        )

    def system_prompt(self) -> str:
        plan = self.study_plan
        syllabus = "\n".join(f"{index}. {item}" for index, item in enumerate(plan.syllabus, start=1))
        specialists = "\n".join(
            f"- {selected.archetype}: {ARCHETYPES[selected.archetype].description}"
            for selected in plan.archetypes
        )
        return SUPERVISOR_PROMPT.format(
            subject=plan.subject,
            supervisor_focus=plan.supervisor_focus,
            syllabus=syllabus,
            specialists=specialists,
        ) + f"\n\n{MATH_FORMAT_RULE}\n{DOCUMENT_TOOL_RULES}"
