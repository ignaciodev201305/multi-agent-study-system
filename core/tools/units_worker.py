"""Proceso aislado de unidades (pint): conversión y análisis dimensional."""

import math
import re
import sys
from pathlib import Path

# Con -I el directorio del script no entra en sys.path; se agrega para reutilizar el worker de math.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from math_worker import serve  # noqa: E402

MAX_UNIT_CHARS = 60
# Nombres de unidades, exponentes y los operadores de una unidad compuesta; nada más.
UNIT_PATTERN = re.compile(r"[A-Za-zµμΩ°0-9 */^().\-]+")


def _unit(text, name: str) -> str:
    """Valida el texto de una unidad contra la lista blanca."""
    text = str(text or "").strip()
    if not text or len(text) > MAX_UNIT_CHARS:
        raise ValueError(f"{name} está vacía o es demasiado larga.")
    if not UNIT_PATTERN.fullmatch(text) or "__" in text:
        raise ValueError(f"{name} solo admite nombres de unidades, números y * / ^ ( ).")
    return text.replace("^", "**")


def _dimensions(quantity) -> str:
    """Describe la dimensión de una magnitud; vacía significa adimensional."""
    return str(quantity.dimensionality) or "adimensional"


def run(request: dict) -> dict:
    """Convierte una magnitud o compara las dimensiones de dos unidades."""
    import pint

    registry = pint.UnitRegistry()
    value = float(request.get("value", 1))
    if not math.isfinite(value):
        raise ValueError("value debe ser un número finito.")
    try:
        quantity = registry.Quantity(value, _unit(request.get("from_unit"), "from_unit"))
        if not request.get("to_unit"):
            base = quantity.to_base_units()
            return {"result": f"{base.magnitude:.6g} {base.units} (SI)\nDimensión: {_dimensions(quantity)}"}
        target = registry.Unit(_unit(request["to_unit"], "to_unit"))
        converted = quantity.to(target)
    except pint.UndefinedUnitError as error:
        raise ValueError(f"Unidad desconocida: {error}") from None
    except pint.DimensionalityError:
        other = registry.Quantity(1, target)
        raise ValueError(
            f"Dimensiones incompatibles: {request['from_unit']} es {_dimensions(quantity)} "
            f"y {request['to_unit']} es {_dimensions(other)}."
        ) from None
    return {"result": f"{converted.magnitude:.6g} {converted.units}\nDimensión: {_dimensions(quantity)}"}


if __name__ == "__main__":
    serve(run)
