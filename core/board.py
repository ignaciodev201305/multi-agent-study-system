"""Pizarra de la sesión: bloques validados y operaciones que devuelven el parche a emitir."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.settings import (
    BOARD_MAX_BLOCK_CHARS,
    BOARD_MAX_BLOCKS,
    BOARD_MAX_OPS_PER_TURN,
    BOARD_MAX_PLOT_FUNCTIONS,
)

MAX_TITLE_CHARS = 120
MAX_PLOT_MARKS = 12
BLOCK_METADATA = {"id", "author", "title"}


class BoardError(ValueError):
    """Operación rechazada; su mensaje indica al modelo cómo corregirla."""


class _BoardModel(BaseModel):
    """Base de los bloques: rechaza campos desconocidos."""
    model_config = ConfigDict(extra="forbid")


class MarkdownBlock(_BoardModel):
    """Texto con formato Markdown."""
    kind: Literal["markdown"]
    text: str = Field(min_length=1)


class MathBlock(_BoardModel):
    """Fórmula en LaTeX, sin delimitadores."""
    kind: Literal["math"]
    latex: str = Field(min_length=1)


class Step(_BoardModel):
    """Paso de un procedimiento con su fórmula opcional."""
    text: str = Field(min_length=1)
    latex: str | None = None


class StepsBlock(_BoardModel):
    """Procedimiento paso a paso."""
    kind: Literal["steps"]
    steps: list[Step] = Field(min_length=1)


class TableBlock(_BoardModel):
    """Tabla de texto con encabezados."""
    kind: Literal["table"]
    headers: list[str] = Field(min_length=1)
    rows: list[list[str]]

    @model_validator(mode="after")
    def _check_row_width(self) -> "TableBlock":
        """Exige que cada fila tenga tantas celdas como encabezados."""
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("Cada fila debe tener tantas celdas como encabezados.")
        return self


class CodeBlock(_BoardModel):
    """Código de solo lectura."""
    kind: Literal["code"]
    language: Literal["python", "text"]
    code: str = Field(min_length=1)


class PlotFunction(_BoardModel):
    """Función de x que se dibuja en un gráfico."""
    expression: str = Field(min_length=1)
    label: str | None = None


class PlotMark(_BoardModel):
    """Punto destacado de un gráfico."""
    x: float
    y: float
    label: str | None = None


class PlotBlock(_BoardModel):
    """Gráfico de una o más funciones de x."""
    kind: Literal["plot"]
    functions: list[PlotFunction] = Field(min_length=1)
    x_min: float
    x_max: float
    y_min: float | None = None
    y_max: float | None = None
    points: list[PlotMark] | None = Field(default=None, max_length=MAX_PLOT_MARKS)
    vlines: list[float] | None = Field(default=None, max_length=MAX_PLOT_MARKS)

    @model_validator(mode="after")
    def _check_ranges(self) -> "PlotBlock":
        """Exige rangos con el mínimo por debajo del máximo."""
        if not self.x_min < self.x_max:
            raise ValueError("x_min debe ser menor que x_max.")
        if self.y_min is not None and self.y_max is not None and not self.y_min < self.y_max:
            raise ValueError("y_min debe ser menor que y_max.")
        return self


BoardBlock = Annotated[
    MarkdownBlock | MathBlock | StepsBlock | TableBlock | CodeBlock | PlotBlock,
    Field(discriminator="kind"),
]


class Board(BaseModel):
    """Pizarra de una sesión; `ops_used` vale para el turno y no va al checkpoint."""
    blocks: list[dict] = Field(default_factory=list)
    revision: int = 0
    next_id: int = 1
    ops_used: int = Field(default=0, exclude=True)


def validate_block(block: BoardBlock) -> None:
    """Aplica los límites del bloque; los puntos de un gráfico no cuentan como contenido."""
    if isinstance(block, PlotBlock) and len(block.functions) > BOARD_MAX_PLOT_FUNCTIONS:
        raise BoardError(f"Un gráfico admite hasta {BOARD_MAX_PLOT_FUNCTIONS} funciones.")
    if len(block.model_dump_json()) > BOARD_MAX_BLOCK_CHARS:
        raise BoardError(
            f"El bloque supera {BOARD_MAX_BLOCK_CHARS} caracteres: divídelo o resume su contenido."
        )


def _check_ops(board: Board) -> None:
    """Rechaza la operación si el turno agotó su cupo."""
    if board.ops_used >= BOARD_MAX_OPS_PER_TURN:
        raise BoardError(
            f"Se alcanzó el límite de {BOARD_MAX_OPS_PER_TURN} cambios por turno: "
            "sigue en el chat y continúa la pizarra en el próximo turno."
        )


def _find(board: Board, block_id: str) -> int:
    """Devuelve la posición de un bloque o explica cómo encontrarlo."""
    for index, block in enumerate(board.blocks):
        if block["id"] == block_id:
            return index
    raise BoardError(f"No existe el bloque {block_id}: consulta los ids con get_board.")


def _content(block: BoardBlock, samples: list | None) -> dict:
    """Convierte el bloque en JSON y agrega los puntos de cada función."""
    content = block.model_dump(exclude_none=True)
    for function, points in zip(content.get("functions", []), samples or []):
        function["points"] = points
    return content


def _fold(text: str) -> str:
    """Ignora mayúsculas y espacios repetidos."""
    return " ".join(text.lower().split())


def _duplicate_key(block: dict) -> dict:
    """Clave para comparar bloques; LaTeX y código no se pliegan porque ahí la mayúscula importa."""
    key = {name: value for name, value in block.items() if name not in BLOCK_METADATA}
    if key["kind"] == "plot":
        expressions = sorted(function["expression"] for function in key["functions"])
        return {"kind": "plot", "expressions": expressions, "x_min": key["x_min"], "x_max": key["x_max"]}
    if key["kind"] == "markdown":
        key["text"] = _fold(key["text"])
    elif key["kind"] == "steps":
        key["steps"] = [{**step, "text": _fold(step["text"])} for step in key["steps"]]
    elif key["kind"] == "table":
        key["headers"] = [_fold(header) for header in key["headers"]]
        key["rows"] = [[_fold(cell) for cell in row] for row in key["rows"]]
    return key


def _check_duplicate(board: Board, content: dict) -> None:
    """Rechaza un bloque cuyo contenido ya está en la pizarra."""
    key = _duplicate_key(content)
    for block in board.blocks:
        if _duplicate_key(block) == key:
            raise BoardError(f"Ese contenido ya está en la pizarra como {block['id']}: refiérete a ese bloque.")


def _clean_title(title: str | None) -> str:
    """Normaliza los espacios del título y lo acorta al máximo permitido."""
    return " ".join((title or "").split())[:MAX_TITLE_CHARS]


def _patch(board: Board, op: str, **fields) -> dict:
    """Cuenta la operación, avanza la revisión y arma el evento para el cliente."""
    board.ops_used += 1
    board.revision += 1
    return {"type": "board_patch", "content": "", "revision": board.revision, "op": op, **fields}


def add_block(
    board: Board,
    block: BoardBlock,
    *,
    author: str,
    title: str | None = None,
    samples: list | None = None,
) -> dict:
    """Agrega un bloque con id del servidor y devuelve el parche."""
    validate_block(block)
    content = _content(block, samples)
    _check_duplicate(board, content)
    _check_ops(board)
    if len(board.blocks) >= BOARD_MAX_BLOCKS:
        raise BoardError(
            f"La pizarra llegó a {BOARD_MAX_BLOCKS} bloques: edita o borra bloques antes de agregar otro."
        )
    stored = {"id": f"b{board.next_id}", "author": author}
    clean_title = _clean_title(title)
    if clean_title:
        stored["title"] = clean_title
    stored.update(content)
    board.next_id += 1
    board.blocks.append(stored)
    return _patch(board, "upsert", block=stored)


def update_block(
    board: Board,
    block_id: str,
    block: BoardBlock,
    *,
    title: str | None = None,
    samples: list | None = None,
) -> dict:
    """Reemplaza el contenido de un bloque; sin título nuevo conserva el actual."""
    validate_block(block)
    _check_ops(board)
    index = _find(board, block_id)
    current = board.blocks[index]
    stored = {key: current[key] for key in ("id", "author", "title") if key in current}
    clean_title = _clean_title(title)
    if clean_title:
        stored["title"] = clean_title
    stored.update(_content(block, samples))
    board.blocks[index] = stored
    return _patch(board, "upsert", block=stored)


def remove_block(board: Board, block_id: str) -> dict:
    """Borra un bloque y devuelve el parche."""
    _check_ops(board)
    del board.blocks[_find(board, block_id)]
    return _patch(board, "remove", block_id=block_id)


def clear(board: Board) -> dict:
    """Vacía la pizarra sin reiniciar ids ni revisión."""
    _check_ops(board)
    board.blocks = []
    return _patch(board, "clear")


def block_details(board: Board, block_id: str) -> str:
    """Devuelve el contenido de un bloque en JSON, sin los puntos de sus gráficos."""
    block = board.blocks[_find(board, block_id)]
    details = {key: value for key, value in block.items() if key != "functions"}
    if "functions" in block:
        details["functions"] = [
            {key: value for key, value in function.items() if key != "points"}
            for function in block["functions"]
        ]
    return json.dumps(details, ensure_ascii=False)


def board_inventory(blocks: list[dict], limit: int | None = None) -> str:
    """Lista id, tipo y título de los bloques más recientes, sin su contenido."""
    shown = blocks[-limit:] if limit else blocks
    lines = [f"- {block['id']} ({block['kind']}): {block.get('title', 'sin título')}" for block in shown]
    hidden = len(blocks) - len(shown)
    if hidden:
        lines.insert(0, f"- {hidden} bloques anteriores; lista completa con get_board.")
    return "\n".join(lines)
