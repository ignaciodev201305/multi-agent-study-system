"""Proceso aislado de cálculo numérico (SciPy): EDOs, raíces, mínimos e integrales definidas."""

import math
import re
import sys
from pathlib import Path

# Con -I el directorio del script no entra en sys.path; se agrega para reutilizar el worker de math.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from math_worker import parse_expression, serve  # noqa: E402

MAX_EXPRESSIONS = 4
MAX_VARIABLES = 4
NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,19}")
# Puntos de cada curva de una EDO; caben en un bloque chart.
ODE_POINTS = 200
TIME_NAME = "t"
# NumPy y SciPy reservan más memoria virtual que SymPy al importarse.
MEMORY_BYTES = 2 * 1024 * 1024 * 1024


def _number(value: float) -> str:
    """Formatea un número con seis cifras significativas."""
    return f"{float(value):.6g}"


def _numbers(values, name: str, size: int) -> list[float]:
    """Valida una lista de números finitos del largo esperado."""
    if not isinstance(values, list) or len(values) != size:
        raise ValueError(f"{name} necesita {size} valores.")
    numbers = [float(value) for value in values]
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError(f"{name} debe tener solo números finitos.")
    return numbers


def _symbols(names) -> list:
    """Convierte los nombres de variable validados en símbolos."""
    import sympy

    if not isinstance(names, list) or not 1 <= len(names) <= MAX_VARIABLES:
        raise ValueError(f"variables admite de 1 a {MAX_VARIABLES} nombres.")
    for name in names:
        if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) or "__" in name:
            raise ValueError(f"Nombre de variable no válido: {name}.")
    if len(set(names)) != len(names):
        raise ValueError("variables no puede repetir nombres.")
    return [sympy.Symbol(name) for name in names]


def _functions(expressions, symbols: list, count: int | None = None) -> list:
    """Compila las expresiones validadas a funciones numéricas de las variables dadas."""
    import sympy

    if not isinstance(expressions, list) or not 1 <= len(expressions) <= MAX_EXPRESSIONS:
        raise ValueError(f"expressions admite de 1 a {MAX_EXPRESSIONS} expresiones.")
    if count is not None and len(expressions) != count:
        raise ValueError(f"Se necesitan {count} expresiones, una por variable.")
    functions = []
    for text in expressions:
        text = str(text)
        if text.count("=") > 1:
            raise ValueError("Cada ecuación admite un solo '='.")
        left, _, right = text.partition("=")
        expression = parse_expression(left) - (parse_expression(right) if right.strip() else 0)
        unknown = expression.free_symbols - set(symbols)
        if unknown:
            raise ValueError(f"Variables sin declarar: {sorted(map(str, unknown))}.")
        functions.append(sympy.lambdify(symbols, expression, modules="math"))
    return functions


def _ode(request: dict) -> dict:
    """Integra un sistema de EDOs de primer orden con valor inicial."""
    import sympy
    from scipy.integrate import solve_ivp

    symbols = _symbols(request.get("variables"))
    if any(symbol.name == TIME_NAME for symbol in symbols):
        raise ValueError("t es el tiempo: usa otro nombre para las variables.")
    functions = _functions(request.get("expressions"), [sympy.Symbol(TIME_NAME), *symbols], len(symbols))
    initial = _numbers(request.get("initial"), "initial", len(symbols))
    start, end = _numbers(request.get("span"), "span", 2)
    if not start < end:
        raise ValueError("span debe ser [t inicial, t final], con el inicial menor.")

    solution = solve_ivp(
        lambda time, state: [function(time, *state) for function in functions],
        (start, end),
        initial,
        method="LSODA",
        t_eval=[start + (end - start) * index / (ODE_POINTS - 1) for index in range(ODE_POINTS)],
        rtol=1e-8,
        atol=1e-10,
    )
    if not solution.success:
        raise ValueError(f"No se pudo integrar: {solution.message}")
    lines = [f"En t = {_number(end)}:"]
    series = []
    for symbol, values in zip(symbols, solution.y):
        lines.append(
            f"- {symbol} = {_number(values[-1])} (mínimo {_number(values.min())}, máximo {_number(values.max())})"
        )
        series.append({
            "label": symbol.name,
            "mark": "line",
            "points": [[float(time), float(value)] for time, value in zip(solution.t, values)],
        })
    return {"result": "\n".join(lines), "chart": {"series": series, "x_label": TIME_NAME}}


def _root(request: dict) -> dict:
    """Busca una raíz de un sistema de ecuaciones cerca de un valor inicial."""
    from scipy.optimize import root

    symbols = _symbols(request.get("variables"))
    functions = _functions(request.get("expressions"), symbols, len(symbols))
    guess = _numbers(request.get("initial"), "initial", len(symbols))
    solution = root(lambda point: [function(*point) for function in functions], guess)
    if not solution.success:
        raise ValueError(f"No convergió desde ese valor inicial: {solution.message}")
    values = ", ".join(f"{symbol} = {_number(value)}" for symbol, value in zip(symbols, solution.x))
    residual = max(abs(value) for value in solution.fun)
    return {"result": f"{values}\nResiduo máximo: {_number(residual)}"}


def _minimize(request: dict) -> dict:
    """Busca un mínimo local de una función cerca de un valor inicial."""
    from scipy.optimize import minimize

    symbols = _symbols(request.get("variables"))
    (objective,) = _functions(request.get("expressions"), symbols, 1)
    guess = _numbers(request.get("initial"), "initial", len(symbols))
    bounds = request.get("bounds")
    if bounds is not None:
        if not isinstance(bounds, list) or len(bounds) != len(symbols):
            raise ValueError("bounds necesita un par [mínimo, máximo] por variable.")
        bounds = [tuple(_numbers(pair, "bounds", 2)) for pair in bounds]
        if any(low >= high for low, high in bounds):
            raise ValueError("En bounds el mínimo debe ser menor que el máximo.")
    solution = minimize(lambda point: objective(*point), guess, bounds=bounds)
    if not solution.success:
        raise ValueError(f"No convergió desde ese valor inicial: {solution.message}")
    values = ", ".join(f"{symbol} = {_number(value)}" for symbol, value in zip(symbols, solution.x))
    return {"result": f"Mínimo local en {values}\nValor de la función: {_number(solution.fun)}"}


def _integrate(request: dict) -> dict:
    """Calcula una integral definida de una variable."""
    from scipy.integrate import quad

    symbols = _symbols(request.get("variables"))
    if len(symbols) != 1:
        raise ValueError("integrate usa una sola variable.")
    (function,) = _functions(request.get("expressions"), symbols, 1)
    lower, upper = _numbers(request.get("span"), "span", 2)
    value, error = quad(function, lower, upper, limit=200)
    return {"result": f"Integral = {_number(value)}\nError estimado: {_number(error)}"}


OPERATIONS = {"ode": _ode, "root": _root, "minimize": _minimize, "integrate": _integrate}


def run(request: dict) -> dict:
    """Ejecuta la operación numérica pedida."""
    operation = OPERATIONS.get(request.get("operation"))
    if operation is None:
        raise ValueError(f"Operación no soportada: {request.get('operation')}")
    return operation(request)


if __name__ == "__main__":
    serve(run, MEMORY_BYTES)
