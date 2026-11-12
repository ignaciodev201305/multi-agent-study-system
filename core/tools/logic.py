"""Tool de lógica booleana: tabla de verdad, simplificación y equivalencia en un worker aislado."""

from pathlib import Path
from typing import Literal

from langchain.tools import ToolRuntime, tool

from config.settings import TOOL_WORKER_TIMEOUT_SECONDS
from core.board import TableBlock
from core.tools.board import publish_block
from core.tools.context import TurnContext
from core.tools.symbolic import run_worker

WORKER = Path(__file__).with_name("logic_worker.py")


@tool("logic")
async def logic_tool(
    operation: Literal["truth_table", "simplify", "equivalent"],
    expression: str,
    runtime: ToolRuntime[TurnContext],
    other: str | None = None,
    form: Literal["sop", "pos"] = "sop",
    board_title: str | None = None,
) -> str:
    """Lógica booleana exacta. Úsala para verificar tablas de verdad y simplificaciones.

    expression: variables (A, B, x1) con & (y), | (o), ~ (no), ^ (xor), >> (implica), 0 y 1.
    Hasta 8 variables.
    - truth_table: mintérminos; con board_title deja la tabla en la pizarra (hasta 6 variables).
    - simplify: forma mínima, suma de productos (sop) o producto de sumas (pos), como en Karnaugh.
    - equivalent: compara expression con other y da un contraejemplo si difieren.
    """
    response = await run_worker(
        WORKER,
        {"operation": operation, "expression": expression, "other": other, "form": form},
        TOOL_WORKER_TIMEOUT_SECONDS,
    )
    if "error" in response:
        return f"Error: {response['error']}"
    if operation != "truth_table":
        latex = f"\nLaTeX: {response['latex']}" if "latex" in response else ""
        return f"Resultado: {response['result']}{latex}"

    variables = response["variables"]
    summary = f"Variables: {', '.join(variables)}. Mintérminos: {response['minterms'] or 'ninguno'}."
    if not board_title:
        return summary
    if "rows" not in response:
        return f"{summary}\nLa tabla no se agregó: admite hasta 6 variables."
    block = TableBlock(kind="table", headers=[*variables, "F"], rows=response["rows"])
    return f"{summary}\n{publish_block(runtime, block, board_title)}"
