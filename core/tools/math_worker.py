"""Proceso aislado de SymPy: lee una operación JSON por stdin y escribe el resultado por stdout.

Se ejecuta con `python -I` desde core/tools/math.py, con timeout. `parse_expr` usa `eval`,
así que además de aislarlo en un subproceso la expresión pasa por una lista blanca de
caracteres y nombres antes de parsearse.
"""

import json
import re
import sys

MAX_EXPRESSION_CHARS = 500
ALLOWED_CHARS = re.compile(r"[0-9A-Za-z_+\-*/^().,= ]*")
ALLOWED_FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "asin", "acos", "atan", "sinh", "cosh", "tanh",
    "exp", "log", "ln", "sqrt", "Abs", "factorial", "binomial", "floor", "ceiling",
    "pi", "E", "I", "oo",
}


def _limit_resources() -> None:
    try:
        import resource
    except ImportError:  # Windows: solo queda el timeout del proceso padre.
        return
    memory = 512 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))


def _check(expression: str) -> str:
    expression = expression.strip()
    if not expression or len(expression) > MAX_EXPRESSION_CHARS:
        raise ValueError("Expresión vacía o demasiado larga.")
    if not ALLOWED_CHARS.fullmatch(expression) or "__" in expression:
        raise ValueError("La expresión contiene caracteres no permitidos.")
    for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", expression):
        if len(name) > 3 and name not in ALLOWED_FUNCTIONS:
            raise ValueError(f"Nombre no permitido: {name}. Usa variables cortas (x, y, t, k1).")
    return expression


def _parse(expression: str):
    import sympy
    from sympy.parsing.sympy_parser import (
        convert_xor,
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    namespace = {name: getattr(sympy, name) for name in ALLOWED_FUNCTIONS if hasattr(sympy, name)}
    namespace["ln"] = sympy.log
    global_namespace = {
        name: getattr(sympy, name)
        for name in ("Integer", "Float", "Rational", "Symbol", "Function")
    }
    transformations = standard_transformations + (convert_xor, implicit_multiplication_application)
    return parse_expr(
        _check(expression),
        local_dict=namespace,
        global_dict=global_namespace,
        transformations=transformations,
    )


def _equation(text: str):
    import sympy

    if text.count("=") > 1:
        raise ValueError("Cada ecuación debe tener un solo '='.")
    if "=" in text:
        left, right = text.split("=")
        return sympy.Eq(_parse(left), _parse(right))
    return _parse(text)


def run(request: dict) -> dict:
    import sympy

    operation = request["operation"]
    expression = request["expression"]
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
        result = sympy.limit(target, variable or _single_symbol(target), _parse(str(request["point"])))
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
    symbols = sorted(expression.free_symbols, key=str)
    if len(symbols) != 1:
        raise ValueError("Indica la variable: la expresión tiene varias o ninguna.")
    return symbols[0]


if __name__ == "__main__":
    _limit_resources()
    try:
        response = run(json.loads(sys.stdin.buffer.read().decode("utf-8")))
    except Exception as error:  # El error vuelve al agente como texto, no como excepción.
        response = {"error": f"{type(error).__name__}: {error}"}
    # Bytes UTF-8 explícitos: la codificación por defecto de stdout depende del sistema.
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
