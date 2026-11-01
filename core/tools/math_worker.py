"""Proceso aislado de SymPy que lee una operación JSON por stdin y escribe el resultado por stdout."""

import json
import math
import re
import sys

MAX_EXPRESSION_CHARS = 500
MAX_NAME_CHARS = 20
SAMPLE_MAX_POINTS = 300
SAMPLE_MAX_ABS = 1e6
MATRIX_MAX_SIZE = 6
MATRIX_OPERATIONS = {"det", "inverse", "rank", "eigenvals"}
SERIES_DEFAULT_ORDER = 6
ALLOWED_CHARS = re.compile(r"[0-9A-Za-z_+\-*/^().,= ]*")
ALLOWED_FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan", "sinh", "cosh", "tanh",
    "exp", "log", "ln", "sqrt", "Abs", "factorial", "binomial", "floor", "ceiling",
}
CONSTANTS = {"pi": "pi", "E": "E", "e": "E", "I": "I", "oo": "oo"}
# Estos nombres serían funciones sin definir; otra letra antes de "(" multiplica.
UNDEFINED_FUNCTIONS = {"f", "g", "h"}
UNICODE_REPLACEMENTS = {
    "²": "^2", "³": "^3", "·": "*", "×": "*", "÷": "/", "−": "-", "π": "pi", "∞": "oo",
    "α": "alpha", "β": "beta", "γ": "gamma", "θ": "theta", "λ": "lambda_", "μ": "mu", "ω": "omega",
}


def _limit_resources() -> None:
    """Limita la memoria del proceso cuando el sistema lo permite."""
    try:
        import resource
    except ImportError:
        # Windows no tiene resource; queda el timeout del proceso padre.
        return
    memory = 512 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))


def _check(expression: str) -> str:
    """Valida la expresión contra la lista blanca y la normaliza."""
    expression = str(expression).strip()
    for symbol, replacement in UNICODE_REPLACEMENTS.items():
        expression = expression.replace(symbol, replacement)
    if not expression or len(expression) > MAX_EXPRESSION_CHARS:
        raise ValueError("Expresión vacía o demasiado larga.")
    if "\\" in expression or "{" in expression:
        raise ValueError("Usa sintaxis tipo Python, no LaTeX: (1)/(2), sqrt(x), x^2.")
    if not ALLOWED_CHARS.fullmatch(expression) or "__" in expression:
        raise ValueError("La expresión contiene caracteres no permitidos.")
    if re.search(r"[A-Za-z_]\s*\.", expression):
        raise ValueError("No se permite el acceso a atributos.")
    for match in re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", expression):
        name = match.group()
        if len(name) > MAX_NAME_CHARS:
            raise ValueError(f"Nombre demasiado largo: {name}.")
        is_call = expression[match.end():].lstrip().startswith("(")
        if is_call and name not in ALLOWED_FUNCTIONS and (len(name) > 1 or name in UNDEFINED_FUNCTIONS):
            raise ValueError(f"Función no permitida: {name}. Escribe la expresión explícita.")
    return expression


def _parse(expression: str):
    """Convierte una expresión validada en un objeto de SymPy."""
    import sympy
    from sympy.parsing.sympy_parser import (
        convert_xor,
        function_exponentiation,
        implicit_application,
        implicit_multiplication,
        parse_expr,
        standard_transformations,
    )

    namespace = {name: getattr(sympy, name) for name in ALLOWED_FUNCTIONS if hasattr(sympy, name)}
    namespace["ln"] = sympy.log
    namespace.update({name: getattr(sympy, value) for name, value in CONSTANTS.items()})
    global_namespace = {
        name: getattr(sympy, name)
        for name in ("Integer", "Float", "Rational", "Symbol", "Function")
    }
    # Sin split_symbols para no separar nombres como velocity.
    transformations = standard_transformations + (
        convert_xor,
        implicit_multiplication,
        implicit_application,
        function_exponentiation,
    )
    return parse_expr(
        _check(expression),
        local_dict=namespace,
        global_dict=global_namespace,
        transformations=transformations,
    )


def _equation(text: str):
    """Convierte un texto con '=' en una ecuación de SymPy."""
    import sympy

    if text.count("=") > 1:
        raise ValueError("Cada ecuación debe tener un solo '='.")
    if "=" in text:
        left, right = text.split("=")
        return sympy.Eq(_parse(left), _parse(right))
    return _parse(text)


def _limit(target, symbol, point):
    """Devuelve el límite, o el par izquierda-derecha si los laterales difieren."""
    import sympy

    right = sympy.limit(target, symbol, point, "+")
    if point.is_infinite:
        return right
    left = sympy.limit(target, symbol, point, "-")
    if left.has(sympy.Limit) or right.has(sympy.Limit):
        raise ValueError("No se pudo calcular el límite por ambos lados.")
    # Un lado con valor complejo queda fuera del dominio real.
    if left.is_extended_real is False or left == right or sympy.simplify(left - right) == 0:
        return right
    if right.is_extended_real is False:
        return left
    return left, right


def _sample_value(function, x: float) -> float | None:
    """Evalúa un punto y devuelve None si no es real o supera el recorte."""
    try:
        y = float(function(x))
    except Exception:
        return None
    return y if abs(y) <= SAMPLE_MAX_ABS else None


def _sample(target, symbol, x_min: float, x_max: float, points: int) -> list[list[float | None]]:
    """Muestrea la expresión en puntos equiespaciados; None corta la línea."""
    import sympy

    unknown = target.free_symbols - {symbol}
    if unknown:
        raise ValueError(f"Variables sin valor: {sorted(map(str, unknown))}.")
    if not (math.isfinite(x_min) and math.isfinite(x_max) and x_min < x_max):
        raise ValueError("El intervalo debe ser finito, con x_min menor que x_max.")
    if not 2 <= points <= SAMPLE_MAX_POINTS:
        raise ValueError(f"points debe estar entre 2 y {SAMPLE_MAX_POINTS}.")
    function = sympy.lambdify(symbol, target, modules="math")
    step = (x_max - x_min) / (points - 1)
    samples = []
    for index in range(points):
        x = x_min + step * index
        samples.append([x, _sample_value(function, x)])
    if all(y is None for _, y in samples):
        raise ValueError("La expresión no tiene valores reales dibujables en el intervalo.")
    return samples


def _matrix(rows):
    """Arma la matriz con celdas validadas una por una."""
    import sympy

    if not rows or not all(isinstance(row, list) and row for row in rows):
        raise ValueError("La operación necesita matrix, una lista de filas.")
    if len({len(row) for row in rows}) != 1:
        raise ValueError("Todas las filas de la matriz deben tener el mismo largo.")
    if len(rows) > MATRIX_MAX_SIZE or len(rows[0]) > MATRIX_MAX_SIZE:
        raise ValueError(f"La matriz admite hasta {MATRIX_MAX_SIZE}x{MATRIX_MAX_SIZE}.")
    return sympy.Matrix([[_parse(str(cell)) for cell in row] for row in rows])


def _run_matrix(operation: str, rows) -> dict:
    """Ejecuta una operación de álgebra lineal."""
    import sympy

    matrix = _matrix(rows)
    if operation == "rank":
        result = matrix.rank()
    elif not matrix.is_square:
        raise ValueError("La operación necesita una matriz cuadrada.")
    elif operation == "det":
        result = matrix.det()
    elif operation == "inverse":
        result = matrix.inv()
    else:
        result = matrix.eigenvals()
    return {"result": str(result), "latex": sympy.latex(result)}


def run(request: dict) -> dict:
    """Ejecuta la operación pedida y devuelve su resultado."""
    import sympy

    operation = request["operation"]
    if operation in MATRIX_OPERATIONS:
        return _run_matrix(operation, request.get("matrix"))
    expression = request.get("expression")
    if not expression:
        raise ValueError("La operación necesita expression.")
    variable = sympy.Symbol(_check(request["variable"])) if request.get("variable") else None

    if operation == "simplify":
        result = sympy.simplify(_parse(expression))
    elif operation == "expand":
        result = sympy.expand(_parse(expression))
    elif operation == "factor":
        result = sympy.factor(_parse(expression))
    elif operation == "diff":
        target = _parse(expression)
        result = sympy.diff(target, variable or _single_symbol(target), int(request.get("order") or 1))
    elif operation == "integrate":
        target = _parse(expression)
        symbol = variable or _single_symbol(target)
        lower, upper = request.get("lower"), request.get("upper")
        if lower is not None and upper is not None:
            result = sympy.integrate(target, (symbol, _parse(str(lower)), _parse(str(upper))))
        else:
            result = sympy.integrate(target, symbol)
    elif operation == "limit":
        target = _parse(expression)
        result = _limit(target, variable or _single_symbol(target), _parse(str(request["point"])))
        if isinstance(result, tuple):
            left, right = result
            return {
                "result": f"No existe: por la izquierda {left}, por la derecha {right}",
                "latex": rf"\text{{No existe: }} {sympy.latex(left)} \text{{ por la izquierda, }} "
                         rf"{sympy.latex(right)} \text{{ por la derecha}}",
            }
    elif operation == "series":
        target = _parse(expression)
        center = request.get("point")
        order = int(request.get("order") or SERIES_DEFAULT_ORDER)
        symbol = variable or _single_symbol(target)
        result = sympy.series(target, symbol, _parse(str(center)) if center is not None else 0, order).removeO()
    elif operation == "summation":
        target = _parse(expression)
        lower, upper = request.get("lower"), request.get("upper")
        if lower is None or upper is None:
            raise ValueError("summation necesita lower y upper.")
        result = sympy.summation(target, (variable or _single_symbol(target), _parse(str(lower)), _parse(str(upper))))
    elif operation == "sample":
        target = _parse(expression)
        return {"result": _sample(
            target,
            variable or _single_symbol(target),
            float(request["x_min"]),
            float(request["x_max"]),
            int(request["points"]),
        )}
    elif operation == "solve":
        equations = [_equation(part) for part in expression.split(",")]
        symbols = [variable] if variable else None
        result = sympy.solve(equations if len(equations) > 1 else equations[0], *(symbols or []), dict=True)
        return {"result": str(result), "latex": sympy.latex(result)}
    elif operation == "evaluate":
        target = _parse(expression)
        substitutions = {}
        for assignment in (request.get("substitutions") or "").split(","):
            if assignment.strip():
                name, value = assignment.split("=")
                substitutions[sympy.Symbol(_check(name))] = _parse(value)
        result = sympy.N(target.subs(substitutions), 12)
    else:
        raise ValueError(f"Operación no soportada: {operation}")

    return {"result": str(result), "latex": sympy.latex(result)}


def _single_symbol(expression):
    """Devuelve la única variable de la expresión."""
    symbols = sorted(expression.free_symbols, key=str)
    if len(symbols) != 1:
        raise ValueError("Indica la variable: la expresión tiene varias o ninguna.")
    return symbols[0]


if __name__ == "__main__":
    try:
        _limit_resources()
        response = run(json.loads(sys.stdin.buffer.read().decode("utf-8")))
    except ModuleNotFoundError as error:
        response = {"error": f"{error}. Inicia el backend con el Python de .venv o instala requirements.txt."}
    except Exception as error:
        # El error vuelve al agente como texto.
        response = {"error": f"{type(error).__name__}: {error}"}
    # Escribe bytes UTF-8 porque la codificación de stdout depende del sistema.
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
