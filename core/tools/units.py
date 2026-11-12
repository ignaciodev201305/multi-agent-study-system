"""Tool de unidades: conversión y análisis dimensional en un worker aislado."""

from pathlib import Path

from langchain.tools import tool

from config.settings import TOOL_WORKER_TIMEOUT_SECONDS
from core.tools.symbolic import run_worker

WORKER = Path(__file__).with_name("units_worker.py")


@tool("units")
async def units_tool(value: float, from_unit: str, to_unit: str | None = None) -> str:
    """Convierte unidades y comprueba dimensiones. Úsala antes de mezclar magnitudes.

    from_unit y to_unit: unidades en inglés o por símbolo, compuestas con * / ^
    (p. ej. 'kN/m^2', 'MPa', 'km/h', 'degC', 'lbf*ft'). Sin to_unit pasa al SI.
    Devuelve el valor convertido y la dimensión; si las dimensiones no coinciden, lo indica.
    """
    response = await run_worker(
        WORKER,
        {"value": value, "from_unit": from_unit, "to_unit": to_unit},
        TOOL_WORKER_TIMEOUT_SECONDS,
    )
    if "error" in response:
        return f"Error: {response['error']}"
    return response["result"]
