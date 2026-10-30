"""Grafo padre: un estado y un checkpointer por sesión; contexto o turno según el plan."""

import aiosqlite

from langchain.messages import RemoveMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import START, StateGraph

from config.settings import STUDY_DATABASE_PATH
from core.graphs.context import ContextStudyGraph
from core.graphs.session import SessionStudyGraph
from core.states import StudyState


async def open_checkpointer(database_path=STUDY_DATABASE_PATH) -> AsyncSqliteSaver:
    serde = JsonPlusSerializer(allowed_msgpack_modules=[
        ("core.schemas", "ContextClassification"),
        ("core.schemas", "StudyPlan"),
        ("core.schemas", "SelectedArchetype"),
    ])
    conn = await aiosqlite.connect(str(database_path), check_same_thread=True)
    return AsyncSqliteSaver(conn, serde=serde)


class StudyGraph:
    """Compone el subgrafo de contexto y los nodos del turno de sesión."""

    def __init__(self):
        self.context = ContextStudyGraph()
        self.session = SessionStudyGraph()

    def build_graph(self):
        graph = StateGraph(StudyState)

        def route_turn(state):
            """Sin plan todavía se está contextualizando; con plan es un turno de estudio."""
            return "summarize_conversation" if state.get("study_plan") else "context"

        def reset_history(state):
            """Las aclaraciones ya quedaron en el plan: la conversación de estudio empieza vacía."""
            return {"messages": [RemoveMessage(id=message.id) for message in state["messages"]]}

        graph.add_node("context", self.context.build_subgraph())
        graph.add_node("reset_history", reset_history)
        # El turno va en el padre, no como subgrafo: un subgrafo devuelve su lista final de
        # mensajes y los RemoveMessage del resumidor no llegarían al estado del padre.
        self.session.add_nodes(graph)

        graph.add_conditional_edges(START, route_turn, ["context", "summarize_conversation"])
        graph.add_edge("context", "reset_history")

        self.graph = graph

    async def compile_graph(self, checkpointer=None):
        self.build_graph()
        return self.graph.compile(checkpointer=checkpointer or await open_checkpointer())
