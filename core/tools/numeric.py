"""Tool de cálculo numérico: EDOs, raíces, mínimos e integrales en un worker aislado."""

from pathlib import Path
from typing import Literal

from langchain.tools import ToolRuntime, tool

from config.settings import TOOL_WORKER_TIMEOUT_SECONDS
from core.tools.context import TurnContext
from core.tools.stats import publish_chart
from core.tools.symbolic import run_worker

WORKER = Path(__file__).with_name("numeric_worker.py")


@tool("numeric")
async def numeric_tool(
    operation: Literal["ode", "root", "minimize", "integrate"],
    expressions: list[str],
    variables: list[str],
    runtime: ToolRuntime[TurnContext],
    initial: list[float] | None = None,
    span: list[float] | None = None,
    bounds: list[list[float]] | None = None,
    board_title: str | None = None,
) -> str:
    """Cálculo numérico (SciPy) para lo que math no resuelve en forma cerrada.

    expressions usa la sintaxis de math; variables, hasta 4 nombres.
    - ode: expressions son las derivadas de cada variable respecto de t (p. ej. x' = v, v' = -x:
      ['v', '-x'] con variables ['x', 'v']); initial, el estado inicial; span, [t0, t1].
    - root: sistema 'f = g', una ecuación por variable; initial, el valor de partida.
    - minimize: una función objetivo; initial, el punto de partida; bounds?, [mín, máx] por variable.
    - integrate: una función de una variable; span, [a, b].
    board_title: deja en la pizarra las curvas de ode.
    """
    response = await run_worker(
        WORKER,
        {
            "operation": operation,
            "expressions": expressions,
            "variables": variables,
            "initial": initial,
            "span": span,
            "bounds": bounds,
        },
        TOOL_WORKER_TIMEOUT_SECONDS,
    )
    if "error" in response:
        return f"Error: {response['error']}"
    if board_title and "chart" in response:
        return f"{response['result']}\n{publish_chart(runtime, response['chart'], board_title)}"
    return response["result"]
