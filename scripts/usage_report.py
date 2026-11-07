"""Reporte del uso de LLM registrado en app.db, para medir antes y después de optimizar.

Uso: python -m scripts.usage_report [--chat ID] [--since 2026-11-01] [--turns 10]
Abre la base en solo lectura; no modifica nada.
"""

import argparse
import sqlite3

from config.settings import APP_DATABASE_PATH

ROLE_QUERY = """
    SELECT role, model, COUNT(*) AS calls,
        SUM(input_tokens) AS input_tokens, SUM(cached_tokens) AS cached_tokens,
        SUM(output_tokens) AS output_tokens, SUM(reasoning_tokens) AS reasoning_tokens,
        SUM(cost_usd) AS cost_usd
    FROM model_usage WHERE {where}
    GROUP BY role, model ORDER BY COALESCE(SUM(cost_usd), 0) DESC
"""
TURN_QUERY = """
    SELECT turn_id, MIN(created_at) AS created_at, COUNT(*) AS calls,
        SUM(role = 'supervisor') AS supervisor_calls,
        SUM(input_tokens) AS input_tokens, SUM(cached_tokens) AS cached_tokens,
        SUM(cost_usd) AS cost_usd
    FROM model_usage WHERE source = 'turn' AND {where}
    GROUP BY turn_id ORDER BY calls DESC, input_tokens DESC LIMIT ?
"""


def _percent(part: int | None, total: int | None) -> str:
    return f"{100 * (part or 0) / total:.0f}%" if total else "-"


def _cost(value: float | None) -> str:
    return "-" if value is None else f"${value:.4f}"


def _filters(chat: str | None, since: str | None) -> tuple[str, list]:
    clauses, values = ["1 = 1"], []
    if chat:
        clauses.append("chat_id = ?")
        values.append(chat)
    if since:
        clauses.append("created_at >= ?")
        values.append(since)
    return " AND ".join(clauses), values


def report(connection: sqlite3.Connection, chat: str | None, since: str | None, turns: int) -> str:
    """Arma el reporte en texto: totales, uso por rol y modelo, y los turnos más pesados."""
    where, values = _filters(chat, since)
    connection.row_factory = sqlite3.Row
    roles = connection.execute(ROLE_QUERY.format(where=where), values).fetchall()
    if not roles:
        return "No hay uso registrado con esos filtros."

    total_input = sum(row["input_tokens"] for row in roles)
    total_output = sum(row["output_tokens"] for row in roles)
    lines = [
        f"Llamadas: {sum(row['calls'] for row in roles)}  "
        f"Entrada: {total_input}  en caché: {_percent(sum(row['cached_tokens'] for row in roles), total_input)}  "
        f"Salida: {total_output}  razonamiento: {_percent(sum(row['reasoning_tokens'] for row in roles), total_output)}  "
        f"Costo: {_cost(sum(row['cost_usd'] or 0 for row in roles))}",
        "",
        f"{'rol':<20}{'modelo':<44}{'llamadas':>9}{'entrada/llamada':>17}{'caché':>7}{'razon.':>8}{'costo':>10}",
    ]
    for row in roles:
        lines.append(
            f"{row['role']:<20}{row['model'][:43]:<44}{row['calls']:>9}"
            f"{row['input_tokens'] // row['calls']:>17}{_percent(row['cached_tokens'], row['input_tokens']):>7}"
            f"{_percent(row['reasoning_tokens'], row['output_tokens']):>8}{_cost(row['cost_usd']):>10}"
        )

    heavy = connection.execute(TURN_QUERY.format(where=where), [*values, turns]).fetchall()
    lines += ["", f"Turnos con más llamadas (máx. {turns}):",
              f"{'turno':<10}{'fecha':<27}{'llamadas':>9}{'supervisor':>11}{'entrada':>10}{'caché':>7}{'costo':>10}"]
    for row in heavy:
        lines.append(
            f"{row['turn_id'][:8]:<10}{row['created_at']:<27}{row['calls']:>9}{row['supervisor_calls']:>11}"
            f"{row['input_tokens']:>10}{_percent(row['cached_tokens'], row['input_tokens']):>7}{_cost(row['cost_usd']):>10}"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chat", help="Solo esta sesión.")
    parser.add_argument("--since", help="Desde esta fecha ISO, p. ej. 2026-11-01.")
    parser.add_argument("--turns", type=int, default=10, help="Cuántos turnos pesados listar.")
    arguments = parser.parse_args()
    connection = sqlite3.connect(f"file:{APP_DATABASE_PATH.as_posix()}?mode=ro", uri=True)
    try:
        print(report(connection, arguments.chat, arguments.since, arguments.turns))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
