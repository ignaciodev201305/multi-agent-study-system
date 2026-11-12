"""Tool de vigas: reacciones, cortante, momento flector y flecha en un worker aislado."""

from pathlib import Path
from typing import Literal

from langchain.tools import ToolRuntime, tool

from config.settings import TOOL_WORKER_TIMEOUT_SECONDS
from core.tools.context import TurnContext
from core.tools.stats import publish_chart
from core.tools.symbolic import run_worker

WORKER = Path(__file__).with_name("beam_worker.py")


@tool("beam")
async def beam_tool(
    length: float,
    supports: Literal["simply_supported", "cantilever", "propped_cantilever", "fixed_fixed"],
    runtime: ToolRuntime[TurnContext],
    point_loads: list[list[float]] | None = None,
    distributed_loads: list[list[float]] | None = None,
    moments: list[list[float]] | None = None,
    support_positions: list[float] | None = None,
    ei: float | None = None,
    board_title: str | None = None,
) -> str:
    """Resuelve una viga recta: reacciones, cortante y momento máximos, y flecha si das ei.

    Unidades coherentes (p. ej. m y kN). Empotramientos en x = 0 (cantilever) o en ambos extremos.
    - supports: simply_supported usa apoyos en 0 y length, o en support_positions [a, b].
    - point_loads: [posición, fuerza], hacia abajo positiva.
    - distributed_loads: [inicio, fin, q] uniforme o [inicio, fin, q1, q2] lineal, hacia abajo positiva.
    - moments: [posición, momento], horario positivo.
    - ei: rigidez E·I para la flecha.
    board_title: deja los diagramas de cortante y momento en la pizarra.
    """
    response = await run_worker(
        WORKER,
        {
            "length": length,
            "supports": supports,
            "point_loads": point_loads,
            "distributed_loads": distributed_loads,
            "moments": moments,
            "support_positions": support_positions,
            "ei": ei,
        },
        TOOL_WORKER_TIMEOUT_SECONDS,
    )
    if "error" in response:
        return f"Error: {response['error']}"
    if not board_title:
        return response["result"]
    notices = [publish_chart(runtime, chart, f"{board_title}: {name}") for name, chart in response["charts"]]
    return "\n".join([response["result"], *notices])
