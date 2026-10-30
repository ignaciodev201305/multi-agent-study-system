"""Ejecución de grafos en streaming para enviar tokens y estados al front."""

# Nodos internos cuyos tokens no son parte de la respuesta al alumno.
INTERNAL_NODES = {"summarize_conversation", "build_study_plan"}
# Eventos de las tools que pueden llegar al cliente.
FORWARDED_CUSTOM_EVENTS = {"board_patch"}


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
    # Sin subgraphs no llegan los tokens de los grafos anidados.
    async for namespace, mode, chunk in graph.astream(
        graph_input, config, stream_mode=["messages", "values", "custom"], subgraphs=True
    ):
        if mode == "custom":
            if isinstance(chunk, dict) and chunk.get("type") in FORWARDED_CUSTOM_EVENTS:
                await websocket.send_json(chunk)

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

        # Conserva solo el estado del grafo padre.
        if mode == "values" and not namespace:
            state = chunk
            interrupt_info = chunk.get("__interrupt__")

    return state, interrupt_info
