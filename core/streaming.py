"""Ejecución de grafos en streaming para enviar tokens y estados al front."""

# Nodos internos cuyos tokens no son parte de la respuesta al alumno.
INTERNAL_NODES = {"summarize_conversation", "factory_multi_agent_system"}


def token_text(content) -> str:
    """Texto de un chunk: str tal cual, o la concatenación de sus bloques de texto."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block if isinstance(block, str) else block.get("text", "")
            for block in content
            if isinstance(block, (str, dict))
        )
    return ""


async def execute_astream_graph(graph, websocket, graph_input, config):
    """Recorre el stream del grafo, reenvía tokens y devuelve el último estado e interrupt."""
    state = None
    interrupt_info = None
    async for mode, chunk in graph.astream(graph_input, config, stream_mode=["messages", "values"]):
        if mode == "messages":
            token, metadata = chunk
            node = metadata.get("langgraph_node")
            text = token_text(token.content)
            if text and node not in INTERNAL_NODES:
                await websocket.send_json({
                    "type": "thinking",
                    "content": text,
                    "node": node,
                    "step": metadata.get("langgraph_step")
                })

        if mode == "values":
            state = chunk
            interrupt_info = chunk.get("__interrupt__")

    return state, interrupt_info
