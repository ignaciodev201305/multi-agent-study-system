"""Tool de matemática simbólica (SymPy) ejecutada en un subproceso aislado con timeout."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Literal

from langchain.tools import tool

from config.settings import MATH_TIMEOUT_SECONDS

WORKER = Path(__file__).with_name("math_worker.py")


async def run_math(request: dict) -> dict:
    """Ejecuta la operación en `python -I math_worker.py`; lo mata si supera el timeout."""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(WORKER),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            process.communicate(json.dumps(request).encode("utf-8")),
            timeout=MATH_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return {"error": "El cálculo superó el tiempo máximo; simplifica la expresión."}
    try:
        return json.loads(stdout.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"error": "El cálculo falló."}


@tool("math")
async def math_tool(
    operation: Literal[
        "simplify", "expand", "factor", "diff", "integrate", "limit", "solve", "evaluate",
        "series", "summation", "det", "inverse", "rank", "eigenvals",
    ],
    expression: str | None = None,
    variable: str | None = None,
    order: int | None = None,
    lower: str | int | float | None = None,
    upper: str | int | float | None = None,
    point: str | int | float | None = None,
    substitutions: str | None = None,
    matrix: list[list[str | int | float]] | None = None,
) -> str:
    """Cálculo simbólico exacto (SymPy). Úsala para verificar resultados antes de darlos.

    expression en sintaxis Python, no LaTeX: 3*x^2 + sin(theta), sqrt(x), e^(-t/2), Abs(x).
    e es Euler; sin funciones indefinidas como f(x). log y ln: natural; log10(x); log(x, b).
    Paréntesis explícitos: 1/2x es x/2; escribe 1/(2*x).
    - simplify/expand/factor (simplify toma variables reales).
    - diff: respecto de variable; order para orden n.
    - integrate: primitiva sin constante (log(x) es ln|x|); con lower y upper, definida.
    - limit: variable tiende a point (oo es infinito); si los laterales difieren, da ambos.
    - solve: en los reales, con solución general si es periódica (k entero) y aviso si solo
      hay complejas. También desigualdades 'x^2 > 4' (intervalos) y sistemas
      'x + y = 3, x - y = 1' con variable 'x, y'.
    - evaluate: valor numérico; substitutions 'x=2, y=1/3'.
    - series: Taylor en point (0 por defecto) hasta order términos.
    - summation: expression con variable de lower a upper (oo admitido).
    - det/inverse/rank/eigenvals: matrix de hasta 6x6, p. ej. [["1", "2"], ["3", "x"]]; sin expression.
    """
    response = await run_math({
        "operation": operation,
        "expression": expression,
        "variable": variable,
        "order": order,
        "lower": lower,
        "upper": upper,
        "point": point,
        "substitutions": substitutions,
        "matrix": matrix,
    })
    if "error" in response:
        return f"Error: {response['error']}"
    return f"Resultado: {response['result']}\nLaTeX: {response['latex']}"
