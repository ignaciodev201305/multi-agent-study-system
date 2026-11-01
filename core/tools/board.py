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
from core.tools.context import TurnContext
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


@tool
async def board_add(
    block: BoardBlock,
    runtime: ToolRuntime[TurnContext],
    title: str | None = None,
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
    title: título corto del bloque.
    """
    try:
        validate_block(block)
        samples = await _sample_plot(block)
        patch = add_block(
            runtime.context.board,
            block,
            author=runtime.context.author,
            title=title,
            samples=samples,
        )
    except BoardError as error:
        return f"Error: {error}"
    runtime.stream_writer(patch)
    return f"Bloque agregado: {patch['block']['id']}."


@tool
async def board_edit(
    action: Literal["update", "remove", "clear"],
    runtime: ToolRuntime[TurnContext],
    block_id: str | None = None,
    block: BoardBlock | None = None,
    title: str | None = None,
) -> str:
    """Corrige o borra bloques de la pizarra.

    - update: reemplaza el contenido de block_id por block, con el formato de board_add.
      title cambia el título; si se omite, se conserva.
    - remove: borra block_id.
    - clear: vacía la pizarra; úsalo solo si el alumno lo pide o cambia de tema por completo.
    """
    board = runtime.context.board
    try:
        if action == "clear":
            patch = clear(board)
        elif not block_id:
            return "Error: indica block_id; consulta los ids con get_board."
        elif action == "remove":
            patch = remove_block(board, block_id)
        elif block is None:
            return "Error: update necesita block con el contenido nuevo."
        else:
            validate_block(block)
            patch = update_block(board, block_id, block, title=title, samples=await _sample_plot(block))
    except BoardError as error:
        return f"Error: {error}"
    runtime.stream_writer(patch)
    return f"Pizarra actualizada ({action})."


@tool
async def get_board(runtime: ToolRuntime[TurnContext], block_id: str | None = None) -> str:
    """Lista los bloques de la pizarra con id, tipo y título.

    Con block_id devuelve el contenido de ese bloque; léelo antes de corregirlo con board_edit.
    """
    board = runtime.context.board
    if block_id is None:
        return board_inventory(board.blocks) or "La pizarra está vacía."
    try:
        return block_details(board, block_id)
    except BoardError as error:
        return f"Error: {error}"
