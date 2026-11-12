"""Tool de estadística: descriptiva, distribuciones, tests y regresión en un worker aislado."""

from pathlib import Path
from typing import Literal

from langchain.tools import ToolRuntime, tool
from pydantic import ValidationError

from config.settings import TOOL_WORKER_TIMEOUT_SECONDS
from core.board import ChartBlock
from core.tools.board import publish_block
from core.tools.context import TurnContext
from core.tools.symbolic import run_worker

WORKER = Path(__file__).with_name("stats_worker.py")
MAX_DATA_POINTS = 2000


def publish_chart(runtime: ToolRuntime[TurnContext], chart: dict, title: str) -> str:
    """Valida el gráfico que devolvió un worker y lo agrega a la pizarra."""
    try:
        block = ChartBlock(kind="chart", **chart)
    except ValidationError:
        return "No se agregó a la pizarra: el gráfico no es válido."
    return publish_block(runtime, block, title)


@tool("stats")
async def stats_tool(
    operation: Literal["describe", "distribution", "ttest", "chi2", "regression", "histogram"],
    runtime: ToolRuntime[TurnContext],
    data: list[float] | None = None,
    data_b: list[float] | None = None,
    distribution: Literal["normal", "t", "chi2", "binomial", "poisson", "exponential", "uniform"] | None = None,
    params: list[float] | None = None,
    x: float | None = None,
    p: float | None = None,
    mu: float | None = None,
    bins: int | None = None,
    board_title: str | None = None,
) -> str:
    """Estadística y probabilidad exactas (SciPy). Úsala para verificar antes de dar un resultado.

    data y data_b: listas de hasta 2000 números.
    - describe: media, mediana, desviación, cuartiles e IC 95 % de data.
    - distribution: con params (normal: media, desvío; t y chi2: gl; binomial: n, p; poisson: lambda;
      exponential: tasa; uniform: a, b), da densidad y P(X<=x) en x, o el cuantil de p.
    - ttest: data contra mu, o contra data_b (Welch).
    - chi2: observados en data, esperados en data_b (uniformes si falta).
    - regression: recta de data_b (y) sobre data (x).
    - histogram: frecuencias de data en bins intervalos.
    board_title: deja el gráfico de regression o histogram en la pizarra.
    """
    if any(sample is not None and len(sample) > MAX_DATA_POINTS for sample in (data, data_b)):
        return f"Error: cada lista admite hasta {MAX_DATA_POINTS} valores."
    response = await run_worker(
        WORKER,
        {
            "operation": operation,
            "data": data,
            "data_b": data_b,
            "distribution": distribution,
            "params": params,
            "x": x,
            "p": p,
            "mu": mu,
            "bins": bins,
        },
        TOOL_WORKER_TIMEOUT_SECONDS,
    )
    if "error" in response:
        return f"Error: {response['error']}"
    if board_title and "chart" in response:
        return f"{response['result']}\n{publish_chart(runtime, response['chart'], board_title)}"
    return response["result"]
