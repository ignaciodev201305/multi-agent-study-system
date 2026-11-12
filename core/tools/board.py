"""Tools de la pizarra: agregan y editan bloques, y emiten cada cambio en vivo."""

import asyncio
from typing import Literal

from langchain.tools import ToolRuntime, tool

from core.board import (
    BoardBlock,
    BoardError,
    PlotBlock,
    add_block,
    block_details,
    board_inventory,
    clear,
    remove_block,
    update_block,
    validate_block,
)
from core.tools.context import SUPERVISOR_AUTHOR, TurnContext
from core.tools.symbolic import run_math

PLOT_SAMPLE_POINTS = 200


async def _sample_plot(block: BoardBlock) -> list | None:
    """Calcula los puntos de un gráfico; el resto de los bloques no los lleva."""
    if not isinstance(block, PlotBlock):
        return None
    responses = await asyncio.gather(*(
        run_math({
            "operation": "sample",
            "expression": function.expression,
            "variable": "x",
            "x_min": block.x_min,
            "x_max": block.x_max,
            "points": PLOT_SAMPLE_POINTS,
        })
        for function in block.functions
    ))
    for function, response in zip(block.functions, responses):
        if "result" not in response:
            detail = response.get("error", "el cálculo falló.")
            raise BoardError(f"No se pudo dibujar {function.expression}: {detail}")
    return [response["result"] for response in responses]


def _check_can_replace(context: TurnContext, block_id: str) -> None:
    """El profesor corrige cualquier bloque; un especialista, solo los suyos."""
    if context.author == SUPERVISOR_AUTHOR:
        return
    for block in context.board.blocks:
        if block["id"] == block_id and block["author"] != context.author:
            raise BoardError(f"{block_id} es de {block['author']}: solo puedes reemplazar bloques tuyos.")


def publish_block(runtime: ToolRuntime[TurnContext], block, title: str) -> str:
    """Agrega a la pizarra el resultado de una tool de cálculo y devuelve el aviso para el modelo."""
    context = runtime.context
    try:
        patch = add_block(context.board, block, author=context.author, title=title)
    except BoardError as error:
        return f"No se agregó a la pizarra: {error}"
    runtime.stream_writer(patch)
    block_id = patch["block"]["id"]
    return f"Agregado a la pizarra como {block_id}; cítalo como [[{block_id}]]."


@tool
async def board_add(
    block: BoardBlock,
    runtime: ToolRuntime[TurnContext],
    title: str | None = None,
    topic_number: int | None = None,
    replace_id: str | None = None,
) -> str:
    """Agrega un bloque a la pizarra que el alumno ve junto al chat y devuelve su id.

    Úsala para lo que el alumno consultará después; no para mensajes conversacionales.
    block es uno de estos, según kind:
    - markdown: text, con Markdown y LaTeX entre $...$.
    - math: latex, una fórmula sin delimitadores $.
    - steps: steps, lista de {text, latex?}.
    - table: headers y rows, con celdas de texto.
    - code: language (python o text) y code.
    - plot: functions, lista de {expression, label?}, con x_min, x_max, y_min? e y_max?.
      expression es una función de x con la sintaxis de la tool math, no LaTeX.
      points?: lista de {x, y, label?} para destacar raíces o máximos.
      vlines?: lista de valores de x para marcar asíntotas.
    - exercise: statement, hints? (lista de hasta 5 pistas graduadas) y solution.
      El alumno ve el enunciado y abre pistas y solución cuando quiere; no los repitas en el chat.
    title: título corto del bloque.
    topic_number: número del tema en el TEMARIO, para agrupar el bloque; omítelo si no aplica.
    replace_id: id de un bloque existente para corregirlo en vez de agregar otro; sin title
      ni topic_number conserva los suyos.
    """
    context = runtime.context
    topic = None
    if topic_number is not None:
        name = context.topic_name(topic_number)
        if name is None:
            return f"Error: el tema {topic_number} no existe. Temario:\n{context.numbered_syllabus()}"
        topic = (topic_number, name)
    try:
        validate_block(block)
        samples = await _sample_plot(block)
        if replace_id:
            _check_can_replace(context, replace_id)
            patch = update_block(context.board, replace_id, block, title=title, samples=samples, topic=topic)
        else:
            patch = add_block(
                context.board,
                block,
                author=context.author,
                title=title,
                samples=samples,
                topic=topic,
            )
    except BoardError as error:
        return f"Error: {error}"
    runtime.stream_writer(patch)
    action = "reemplazado" if replace_id else "agregado"
    return f"Bloque {action}: {patch['block']['id']}."


@tool
async def board_edit(
    action: Literal["remove", "clear"],
    runtime: ToolRuntime[TurnContext],
    block_id: str | None = None,
) -> str:
    """Borra bloques de la pizarra. Para corregir uno, usa board_add con replace_id.

    - remove: borra block_id.
    - clear: vacía la pizarra; úsalo solo si el alumno lo pide o cambia de tema por completo.
    """
    # Sin block en la firma: repetir el schema de board_add costaba unos 1500 tokens por llamada.
    board = runtime.context.board
    try:
        if action == "clear":
            patch = clear(board)
        elif not block_id:
            return "Error: indica block_id; consulta los ids con get_board."
        else:
            patch = remove_block(board, block_id)
    except BoardError as error:
        return f"Error: {error}"
    runtime.stream_writer(patch)
    return f"Pizarra actualizada ({action})."


@tool
async def get_board(runtime: ToolRuntime[TurnContext], block_id: str | None = None) -> str:
    """Lista los bloques de la pizarra con id, tipo y título.

    Con block_id devuelve el contenido de ese bloque; léelo antes de corregirlo con board_add y replace_id.
    """
    board = runtime.context.board
    if block_id is None:
        return board_inventory(board.blocks) or "La pizarra está vacía."
    try:
        return block_details(board, block_id)
    except BoardError as error:
        return f"Error: {error}"
