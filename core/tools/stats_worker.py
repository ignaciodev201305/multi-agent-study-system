"""Proceso aislado de estadística (NumPy y SciPy): descriptiva, distribuciones, tests y regresión."""

import math
import sys
from pathlib import Path

# Con -I el directorio del script no entra en sys.path; se agrega para reutilizar el worker de math.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from math_worker import serve  # noqa: E402

MAX_DATA_POINTS = 2000
MAX_BINS = 50
# Puntos que caben en un bloque chart de la pizarra.
MAX_CHART_POINTS = 300
# NumPy y SciPy reservan más memoria virtual que SymPy al importarse.
MEMORY_BYTES = 2 * 1024 * 1024 * 1024
# Cantidad de parámetros de cada distribución, en el orden que documenta la tool.
DISTRIBUTION_PARAMETERS = {
    "normal": 2, "t": 1, "chi2": 1, "binomial": 2, "poisson": 1, "exponential": 1, "uniform": 2,
}
DISCRETE = {"binomial", "poisson"}


def _number(value: float) -> str:
    """Formatea un número con seis cifras significativas."""
    return f"{float(value):.6g}"


def _data(values, name: str, minimum: int = 1) -> list[float]:
    """Valida una muestra: lista acotada de números finitos."""
    if not isinstance(values, list) or len(values) < minimum:
        raise ValueError(f"{name} necesita al menos {minimum} valores.")
    if len(values) > MAX_DATA_POINTS:
        raise ValueError(f"{name} admite hasta {MAX_DATA_POINTS} valores.")
    numbers = [float(value) for value in values]
    if not all(math.isfinite(number) for number in numbers):
        raise ValueError(f"{name} debe tener solo números finitos.")
    return numbers


def _describe(request: dict) -> dict:
    """Resume una muestra con sus medidas de centro, dispersión e intervalo de la media."""
    import numpy
    from scipy import stats

    data = numpy.array(_data(request.get("data"), "data"))
    q1, median, q3 = numpy.percentile(data, [25, 50, 75])
    lines = [
        f"n = {data.size}",
        f"media = {_number(data.mean())}",
        f"mediana = {_number(median)}",
        f"mínimo = {_number(data.min())}, máximo = {_number(data.max())}",
        f"Q1 = {_number(q1)}, Q3 = {_number(q3)}",
    ]
    if data.size > 1:
        deviation = data.std(ddof=1)
        lines.append(f"desviación estándar muestral = {_number(deviation)}, varianza = {_number(deviation ** 2)}")
        if deviation > 0:
            low, high = stats.t.interval(0.95, data.size - 1, loc=data.mean(), scale=stats.sem(data))
            lines.append(f"IC 95 % de la media = [{_number(low)}, {_number(high)}]")
    return {"result": "\n".join(lines)}


def _frozen(name: str, parameters):
    """Construye la distribución con sus parámetros validados."""
    from scipy import stats

    expected = DISTRIBUTION_PARAMETERS.get(name)
    if expected is None:
        raise ValueError("Indica distribution.")
    if not isinstance(parameters, list) or len(parameters) != expected:
        raise ValueError(f"{name} necesita {expected} valores en params.")
    values = [float(value) for value in parameters]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("params debe tener solo números finitos.")
    if name == "normal":
        return stats.norm(values[0], values[1])
    if name == "t":
        return stats.t(values[0])
    if name == "chi2":
        return stats.chi2(values[0])
    if name == "binomial":
        return stats.binom(int(values[0]), values[1])
    if name == "poisson":
        return stats.poisson(values[0])
    if name == "exponential":
        return stats.expon(scale=1 / values[0])
    return stats.uniform(values[0], values[1] - values[0])


def _distribution(request: dict) -> dict:
    """Calcula probabilidades en x y el cuantil de p."""
    name = request.get("distribution")
    frozen = _frozen(name, request.get("params"))
    x, p = request.get("x"), request.get("p")
    if x is None and p is None:
        raise ValueError("Indica x (para probabilidades) o p (para el cuantil).")
    lines = []
    if x is not None:
        x = float(x)
        point = frozen.pmf(x) if name in DISCRETE else frozen.pdf(x)
        lines.append(f"{'P(X = x)' if name in DISCRETE else 'densidad f(x)'} = {_number(point)}")
        lines.append(f"P(X <= x) = {_number(frozen.cdf(x))}")
        lines.append(f"P(X > x) = {_number(frozen.sf(x))}")
    if p is not None:
        p = float(p)
        if not 0 < p < 1:
            raise ValueError("p debe estar entre 0 y 1.")
        lines.append(f"cuantil de p = {_number(frozen.ppf(p))}")
    if any("nan" in line for line in lines):
        raise ValueError("Los parámetros no definen una distribución válida.")
    return {"result": "\n".join(lines)}


def _ttest(request: dict) -> dict:
    """Contrasta una media contra mu o dos medias entre sí (Welch)."""
    from scipy import stats

    data = _data(request.get("data"), "data", minimum=2)
    if request.get("data_b") is not None:
        other = _data(request["data_b"], "data_b", minimum=2)
        test = stats.ttest_ind(data, other, equal_var=False)
        label = "t de Welch para dos muestras"
    else:
        mu = float(request.get("mu") or 0)
        test = stats.ttest_1samp(data, mu)
        label = f"t de una muestra contra mu = {_number(mu)}"
    return {"result": (
        f"{label}\nt = {_number(test.statistic)}, gl = {_number(test.df)}, "
        f"p (bilateral) = {_number(test.pvalue)}"
    )}


def _chi2(request: dict) -> dict:
    """Contrasta frecuencias observadas contra las esperadas (uniformes si no se dan)."""
    from scipy import stats

    observed = _data(request.get("data"), "data", minimum=2)
    expected = None
    if request.get("data_b") is not None:
        expected = _data(request["data_b"], "data_b", minimum=2)
        if len(expected) != len(observed):
            raise ValueError("data y data_b deben tener el mismo largo.")
    test = stats.chisquare(observed, expected)
    return {"result": (
        f"chi2 de bondad de ajuste\nchi2 = {_number(test.statistic)}, gl = {len(observed) - 1}, "
        f"p = {_number(test.pvalue)}"
    )}


def _thin(points: list) -> list:
    """Reduce los puntos a los que caben en un gráfico, espaciados de forma pareja."""
    if len(points) <= MAX_CHART_POINTS:
        return points
    step = (len(points) - 1) / (MAX_CHART_POINTS - 1)
    return [points[round(index * step)] for index in range(MAX_CHART_POINTS)]


def _regression(request: dict) -> dict:
    """Ajusta una recta por mínimos cuadrados."""
    from scipy import stats

    x = _data(request.get("data"), "data", minimum=3)
    y = _data(request.get("data_b"), "data_b", minimum=3)
    if len(x) != len(y):
        raise ValueError("data (x) y data_b (y) deben tener el mismo largo.")
    if len(set(x)) < 2:
        raise ValueError("data (x) necesita al menos dos valores distintos.")
    fit = stats.linregress(x, y)
    pairs = _thin(sorted(zip(x, y)))
    return {
        "result": (
            f"y = {_number(fit.slope)} * x + {_number(fit.intercept)}\n"
            f"r = {_number(fit.rvalue)}, R2 = {_number(fit.rvalue ** 2)}, "
            f"p de la pendiente = {_number(fit.pvalue)}, error estándar = {_number(fit.stderr)}"
        ),
        "chart": {
            "series": [
                {"label": "datos", "mark": "scatter", "points": [[a, b] for a, b in pairs]},
                {"label": "ajuste", "mark": "line", "points": [[a, fit.slope * a + fit.intercept] for a, _ in pairs]},
            ],
            "x_label": "x",
            "y_label": "y",
        },
    }


def _histogram(request: dict) -> dict:
    """Cuenta los datos por intervalos de igual ancho."""
    import numpy

    data = _data(request.get("data"), "data", minimum=2)
    bins = request.get("bins")
    # Regla de Sturges cuando no se pide una cantidad.
    bins = int(bins) if bins else math.ceil(math.log2(len(data))) + 1
    if not 1 <= bins <= MAX_BINS:
        raise ValueError(f"bins debe estar entre 1 y {MAX_BINS}.")
    counts, edges = numpy.histogram(data, bins=bins)
    lines = [
        f"[{_number(edges[index])}, {_number(edges[index + 1])}): {int(count)}"
        for index, count in enumerate(counts)
    ]
    centers = (edges[:-1] + edges[1:]) / 2
    return {
        "result": "Frecuencias por intervalo:\n" + "\n".join(lines),
        "chart": {
            "series": [{
                "label": "frecuencia",
                "mark": "bar",
                "points": [[float(center), float(count)] for center, count in zip(centers, counts)],
            }],
            "x_label": "valor",
            "y_label": "frecuencia",
        },
    }


OPERATIONS = {
    "describe": _describe,
    "distribution": _distribution,
    "ttest": _ttest,
    "chi2": _chi2,
    "regression": _regression,
    "histogram": _histogram,
}


def run(request: dict) -> dict:
    """Ejecuta la operación de estadística pedida."""
    operation = OPERATIONS.get(request.get("operation"))
    if operation is None:
        raise ValueError(f"Operación no soportada: {request.get('operation')}")
    return operation(request)


if __name__ == "__main__":
    serve(run, MEMORY_BYTES)
