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
    """Cálculo simbólico exacto con SymPy. Úsala para verificar resultados antes de darlos.

    expression usa sintaxis tipo Python, no LaTeX: 3*x^2 + sin(theta), sqrt(x), e^(-t/2), log(x).
    e es la constante de Euler. No admite funciones sin definir como f(x).
    - simplify / expand / factor: reescribe la expresión.
    - diff: derivada respecto de variable (order para derivadas de orden n).
    - integrate: primitiva; con lower y upper, integral definida.
    - limit: límite cuando variable tiende a point (usa oo para infinito).
      Si los límites laterales difieren, responde que no existe y da ambos valores.
    - solve: ecuaciones 'x^2 - 4 = 0' (varias separadas por coma), despejando variable.
    - evaluate: valor numérico; substitutions como 'x=2, y=1/3'.
    - series: desarrollo de Taylor en torno a point (0 si se omite), hasta order términos.
    - summation: suma de expression con variable entre lower y upper (usa oo para infinito).
    - det / inverse / rank / eigenvals: álgebra lineal sobre matrix, una lista de filas de hasta
      6x6, p. ej. [["1", "2"], ["3", "x"]]. No llevan expression.
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
