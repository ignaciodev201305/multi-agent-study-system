"""Proceso aislado de lógica booleana (SymPy): tabla de verdad, simplificación y equivalencia."""

import itertools
import keyword
import re
import sys
from pathlib import Path

# Con -I el directorio del script no entra en sys.path; se agrega para reutilizar el worker de math.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from math_worker import serve  # noqa: E402

MAX_EXPRESSION_CHARS = 300
MAX_NAME_CHARS = 10
MAX_VARIABLES = 8
# La tabla crece como 2^n; con más variables solo se informan los mintérminos.
MAX_TABLE_VARIABLES = 6
TOKEN_PATTERN = re.compile(r"\s*(>>|[A-Za-z_][A-Za-z0-9_]*|[01]|[&|~^()])\s*")
CONSTANTS = {"0": "false", "1": "true"}
RESERVED_NAMES = {"true", "false"}


def _tokens(expression: str) -> list[str]:
    """Separa la expresión en tokens de la lista blanca; cualquier otro carácter se rechaza."""
    expression = str(expression).strip()
    if not expression or len(expression) > MAX_EXPRESSION_CHARS:
        raise ValueError("Expresión vacía o demasiado larga.")
    tokens, position = [], 0
    while position < len(expression):
        match = TOKEN_PATTERN.match(expression, position)
        if match is None:
            raise ValueError("Usa variables y los operadores & (y), | (o), ~ (no), ^ (xor), >> (implica), 0 y 1.")
        tokens.append(match.group(1))
        position = match.end()
    return tokens


def _parse(expression: str):
    """Convierte la expresión validada en una fórmula booleana de SymPy."""
    import sympy
    from sympy.parsing.sympy_parser import parse_expr

    tokens = _tokens(expression)
    names = {}
    for index, token in enumerate(tokens):
        if not (token[0].isalpha() or token[0] == "_"):
            continue
        if len(token) > MAX_NAME_CHARS or "__" in token or keyword.iskeyword(token) or token in RESERVED_NAMES:
            raise ValueError(f"Nombre de variable no permitido: {token}.")
        if index + 1 < len(tokens) and tokens[index + 1] == "(":
            raise ValueError(f"No se permiten funciones: {token}. Usa solo operadores.")
        names[token] = sympy.Symbol(token)
    if len(names) > MAX_VARIABLES:
        raise ValueError(f"Se admiten hasta {MAX_VARIABLES} variables.")
    source = " ".join(CONSTANTS.get(token, token) for token in tokens)
    # Sin transformaciones: solo quedan los nombres del diccionario y los operadores validados.
    formula = parse_expr(
        source,
        local_dict={**names, "true": sympy.true, "false": sympy.false},
        global_dict={},
        transformations=(),
    )
    return sympy.sympify(formula)


def _variables(*formulas) -> list:
    """Devuelve las variables de las fórmulas en orden alfabético."""
    return sorted(set().union(*(formula.free_symbols for formula in formulas)), key=str)


def _truth_table(formula) -> dict:
    """Evalúa la fórmula en todas las combinaciones de sus variables."""
    variables = _variables(formula)
    rows, minterms = [], []
    for index, values in enumerate(itertools.product((False, True), repeat=len(variables))):
        value = bool(formula.subs(dict(zip(variables, values))))
        rows.append([*("1" if item else "0" for item in values), "1" if value else "0"])
        if value:
            minterms.append(index)
    result = {"variables": [str(variable) for variable in variables], "minterms": minterms}
    if len(variables) <= MAX_TABLE_VARIABLES:
        result["rows"] = rows
    return result


def _equivalence(formula, other) -> dict:
    """Compara dos fórmulas y da un contraejemplo si difieren."""
    import sympy
    from sympy.logic.inference import satisfiable

    variables = _variables(formula, other)
    if len(variables) > MAX_VARIABLES:
        raise ValueError(f"Se admiten hasta {MAX_VARIABLES} variables entre las dos expresiones.")
    counterexample = satisfiable(sympy.Xor(formula, other))
    if not counterexample:
        return {"result": "Son equivalentes."}
    values = ", ".join(f"{name}={1 if counterexample.get(name) else 0}" for name in variables)
    return {"result": f"No son equivalentes: difieren con {values}."}


def run(request: dict) -> dict:
    """Ejecuta la operación de lógica pedida."""
    import sympy
    from sympy.logic.boolalg import simplify_logic

    operation = request["operation"]
    formula = _parse(request["expression"])
    if operation == "truth_table":
        return _truth_table(formula)
    if operation == "simplify":
        form = "cnf" if request.get("form") == "pos" else "dnf"
        result = simplify_logic(formula, form=form, force=True)
        return {"result": str(result), "latex": sympy.latex(result)}
    if operation == "equivalent":
        if not request.get("other"):
            raise ValueError("equivalent necesita other, la segunda expresión.")
        return _equivalence(formula, _parse(request["other"]))
    raise ValueError(f"Operación no soportada: {operation}")


if __name__ == "__main__":
    serve(run)
