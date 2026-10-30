"""Ejecución de grafos en streaming para enviar tokens y estados al front."""

from langchain.messages import HumanMessage

async def execute_astream_graph(graph, websocket, graph_input, config):
    """Recorre el stream del grafo, reenvía tokens y devuelve el último estado e interrupt."""
    state = None
    interrupt_info = None
    async for mode, chunk in  graph.astream(graph_input, config, stream_mode=["messages", "values"]):
        if mode == "messages":
            token, metadata = chunk
            
            if token.content:
                await websocket.send_json({
                    "type": "thinking",
                    "content": " ".join(token.content),
                    "node": metadata.get("langgraph_node"),
                    "step": metadata.get("langgraph_step")
                })

        if mode == "values":
            state = chunk 
            interrupt_info = chunk.get("__interrupt__")

    return state, interrupt_info