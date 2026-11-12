"""Proceso aislado de vigas (SymPy): reacciones, cortante, momento flector y flecha."""

import math
import sys
from pathlib import Path

# Con -I el directorio del script no entra en sys.path; se agrega para reutilizar el worker de math.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from math_worker import serve  # noqa: E402

MAX_LENGTH = 10_000
MAX_LOADS = 10
SAMPLE_POINTS = 201
# Fracción de la luz a cada lado de una carga puntual, para dibujar el salto del cortante.
JUMP_OFFSET = 1e-9
# Apoyos de cada tipo de viga: (extremo, tiene momento de empotramiento).
SUPPORTS = {
    "cantilever": (("start", True),),
    "propped_cantilever": (("start", True), ("end", False)),
    "fixed_fixed": (("start", True), ("end", True)),
}


def _number(value: float) -> str:
    """Formatea un número con seis cifras significativas; el cero negativo se muestra como cero."""
    value = float(value)
    return f"{0.0 if abs(value) < 1e-12 else value:.6g}"


def _rows(values, name: str, sizes: tuple[int, ...]) -> list[list[float]]:
    """Valida una lista acotada de filas numéricas con uno de los largos permitidos."""
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_LOADS:
        raise ValueError(f"{name} admite hasta {MAX_LOADS} elementos.")
    rows = []
    for row in values:
        if not isinstance(row, list) or len(row) not in sizes:
            raise ValueError(f"Cada elemento de {name} lleva {' o '.join(map(str, sizes))} números.")
        numbers = [float(value) for value in row]
        if not all(math.isfinite(number) for number in numbers):
            raise ValueError(f"{name} debe tener solo números finitos.")
        rows.append(numbers)
    return rows


def _inside(position: float, length: float, name: str) -> float:
    """Comprueba que la posición esté sobre la viga."""
    if not 0 <= position <= length:
        raise ValueError(f"{name}: la posición {position} está fuera de la viga (0 a {length}).")
    return position


def _support_positions(kind: str, positions, length: float) -> list[tuple[float, bool]]:
    """Devuelve la posición de cada apoyo y si es un empotramiento."""
    if kind in SUPPORTS:
        return [(0.0 if end == "start" else length, fixed) for end, fixed in SUPPORTS[kind]]
    if kind != "simply_supported":
        raise ValueError(f"Tipo de viga no soportado: {kind}")
    if positions is None:
        return [(0.0, False), (length, False)]
    (pair,) = _rows([positions], "support_positions", (2,))
    first, second = sorted(_inside(position, length, "support_positions") for position in pair)
    if first == second:
        raise ValueError("Los dos apoyos deben estar en posiciones distintas.")
    return [(first, False), (second, False)]


def _solve(request: dict):
    """Arma la viga, aplica las cargas y resuelve las reacciones."""
    import sympy
    from sympy.physics.continuum_mechanics.beam import Beam

    length = float(request.get("length") or 0)
    if not 0 < length <= MAX_LENGTH:
        raise ValueError(f"length debe ser mayor que 0 y de hasta {MAX_LENGTH}.")
    supports = _support_positions(request.get("supports"), request.get("support_positions"), length)
    point_loads = _rows(request.get("point_loads"), "point_loads", (2,))
    distributed = _rows(request.get("distributed_loads"), "distributed_loads", (3, 4))
    moments = _rows(request.get("moments"), "moments", (2,))
    if not (point_loads or distributed or moments):
        raise ValueError("Indica al menos una carga.")

    elasticity, inertia = sympy.symbols("E I", positive=True)
    beam = Beam(sympy.nsimplify(length), elasticity, inertia)
    reactions, deflection_conditions, slope_conditions = [], [], []
    for index, (position, fixed) in enumerate(supports):
        exact = sympy.nsimplify(position)
        force = sympy.Symbol(f"R{index}")
        beam.apply_load(force, exact, -1)
        reactions.append((force, position, "fuerza"))
        deflection_conditions.append((exact, 0))
        if fixed:
            moment = sympy.Symbol(f"M{index}")
            beam.apply_load(moment, exact, -2)
            reactions.append((moment, position, "momento"))
            slope_conditions.append((exact, 0))
    beam.bc_deflection = deflection_conditions
    beam.bc_slope = slope_conditions

    jumps = {position for position, _ in supports}
    # En SymPy las fuerzas hacia arriba y los momentos horarios son positivos.
    for position, force in point_loads:
        beam.apply_load(-sympy.nsimplify(force), sympy.nsimplify(_inside(position, length, "point_loads")), -1)
        jumps.add(position)
    for position, moment in moments:
        beam.apply_load(sympy.nsimplify(moment), sympy.nsimplify(_inside(position, length, "moments")), -2)
        jumps.add(position)
    for start, end, intensity, *final in distributed:
        if not _inside(start, length, "distributed_loads") < _inside(end, length, "distributed_loads"):
            raise ValueError("distributed_loads: el inicio debe ser menor que el fin.")
        first, last = sympy.nsimplify(start), sympy.nsimplify(end)
        beam.apply_load(-sympy.nsimplify(intensity), first, 0, end=last)
        if final and final[0] != intensity:
            slope = sympy.nsimplify(final[0] - intensity) / (last - first)
            beam.apply_load(-slope, first, 1, end=last)

    beam.solve_for_reaction_loads(*(symbol for symbol, _, _ in reactions))
    return beam, reactions, length, sorted(jumps), elasticity * inertia


def _grid(length: float, jumps: list[float]) -> list[float]:
    """Puntos de muestreo, con uno a cada lado de las cargas puntuales."""
    points = {length * index / (SAMPLE_POINTS - 1) for index in range(SAMPLE_POINTS)}
    offset = length * JUMP_OFFSET
    for position in jumps:
        points.update(value for value in (position - offset, position + offset) if 0 <= value <= length)
    return sorted(points)


def _extreme(grid: list[float], values: list[float]) -> str:
    """Describe el valor de mayor magnitud y dónde ocurre."""
    index = max(range(len(values)), key=lambda item: abs(values[item]))
    return f"{_number(values[index])} en x = {_number(grid[index])}"


def run(request: dict) -> dict:
    """Resuelve la viga y devuelve reacciones, extremos y los diagramas muestreados."""
    import sympy

    beam, reactions, length, jumps, rigidity = _solve(request)
    x = beam.variable
    grid = _grid(length, jumps)

    def sample(expression) -> list[float]:
        # Un momento puntual deja un impulso en el cortante: no es un valor del diagrama.
        expression = expression.replace(
            lambda term: isinstance(term, sympy.SingularityFunction) and term.args[2] < 0,
            lambda term: sympy.Integer(0),
        )
        function = sympy.lambdify(x, expression.rewrite(sympy.Piecewise), "math")
        return [float(function(value)) for value in grid]

    # Convención usual: cortante positivo si la parte izquierda empuja hacia arriba y momento
    # positivo si tracciona la fibra inferior; es la opuesta a la de SymPy.
    shear = sample(-beam.shear_force())
    moment = sample(-beam.bending_moment())

    lines = ["Reacciones (fuerza hacia arriba y momento antihorario, positivos):"]
    for symbol, position, kind in reactions:
        value = float(beam.reaction_loads[symbol])
        lines.append(f"- {kind} en x = {_number(position)}: {_number(value if kind == 'fuerza' else -value)}")
    lines.append(f"Cortante máximo: {_extreme(grid, shear)}")
    lines.append(f"Momento flector máximo (positivo tracciona abajo): {_extreme(grid, moment)}")
    if request.get("ei") is not None:
        ei = float(request["ei"])
        if not (math.isfinite(ei) and ei > 0):
            raise ValueError("ei debe ser un número mayor que 0.")
        deflection = sample(beam.deflection().subs(rigidity, sympy.nsimplify(ei)))
        lines.append(f"Flecha máxima (negativa hacia abajo): {_extreme(grid, deflection)}")

    def chart(values: list[float], label: str) -> dict:
        return {
            "series": [{"label": label, "mark": "line", "points": [[a, b] for a, b in zip(grid, values)]}],
            "x_label": "x",
            "y_label": label,
        }

    return {
        "result": "\n".join(lines),
        "charts": [["cortante", chart(shear, "V")], ["momento flector", chart(moment, "M")]],
    }


if __name__ == "__main__":
    serve(run)
