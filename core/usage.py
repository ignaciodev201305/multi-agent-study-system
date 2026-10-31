"""Registro de tokens y costo por llamada al modelo y su guardado en SQLite."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from langchain_core.callbacks import AsyncCallbackHandler

from core.llm import MODEL_TAG, ROLE_TAG
from core.model_catalog import CatalogUnavailable

logger = logging.getLogger(__name__)

UNKNOWN = "desconocido"
SUPERVISOR_ROLE = "supervisor"
TOKENS_PER_PRICE_UNIT = 1_000_000
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens")


@dataclass
class UsageRecord:
    """Tokens y costo de una llamada al modelo."""
    role: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None
    cost_estimated: bool = False
    source: str = "turn"


def estimate_cost(record: UsageRecord, catalog_entry: dict | None) -> float | None:
    """Calcula el costo con los precios del catálogo, sin descontar la caché."""
    entry = catalog_entry or {}
    prompt_price, completion_price = entry.get("prompt_price"), entry.get("completion_price")
    if prompt_price is None or completion_price is None:
        return None
    cost = record.input_tokens * prompt_price + record.output_tokens * completion_price
    return cost / TOKENS_PER_PRICE_UNIT


def _is_cost(value) -> bool:
    """Indica si el valor es un costo numérico."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _tag_value(tags: list[str], prefix: str) -> str | None:
    """Devuelve el valor de la primera etiqueta con ese prefijo."""
    return next((tag[len(prefix):] for tag in tags if tag.startswith(prefix)), None)


def _reported_cost(metadata: dict, llm_output: dict | None) -> float | None:
    """Busca el costo informado, con o sin streaming."""
    usages = (metadata.get("token_usage"), (llm_output or {}).get("token_usage"))
    candidates = [metadata.get("cost"), *((usage or {}).get("cost") for usage in usages)]
    return next((cost for cost in candidates if _is_cost(cost)), None)


class UsageTracker(AsyncCallbackHandler):
    """Acumula el uso de cada llamada; nunca interrumpe el trabajo que observa."""

    def __init__(self, source: str = "turn", catalog=None, on_record=None):
        self.source = source
        self.catalog = catalog
        self.on_record = on_record
        self.records: list[UsageRecord] = []

    async def on_llm_end(self, response, *, tags=None, **kwargs) -> None:
        """Registra la llamada y avisa al callback si hay uno."""
        try:
            message = response.generations[0][0].message
            usage = getattr(message, "usage_metadata", None)
            if not usage:
                logger.warning("Llamada sin datos de uso tags=%s", tags)
                return
            record = await self._build_record(message, usage, tags or [], response.llm_output)
            self.records.append(record)
            if self.on_record is not None:
                await self.on_record(record)
        except Exception:
            logger.exception("No se pudo registrar el uso de una llamada")

    async def _build_record(self, message, usage: dict, tags: list[str], llm_output) -> UsageRecord:
        """Arma el registro y estima el costo si la respuesta no lo trae."""
        metadata = message.response_metadata or {}
        record = UsageRecord(
            role=_tag_value(tags, ROLE_TAG) or UNKNOWN,
            model=_tag_value(tags, MODEL_TAG) or metadata.get("model_name") or UNKNOWN,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cached_tokens=(usage.get("input_token_details") or {}).get("cache_read", 0),
            reasoning_tokens=(usage.get("output_token_details") or {}).get("reasoning", 0),
            cost_usd=_reported_cost(metadata, llm_output),
            source=self.source,
        )
        if record.cost_usd is None:
            record.cost_usd = estimate_cost(record, await self._catalog_entry(record.model))
            record.cost_estimated = record.cost_usd is not None
        return record

    async def _catalog_entry(self, model: str) -> dict | None:
        """Busca el modelo en el catálogo, si se puede consultar."""
        if self.catalog is None:
            return None
        try:
            models = await self.catalog.list_models()
        except CatalogUnavailable:
            return None
        return next((entry for entry in models if entry["id"] == model), None)


class UsageRepository:
    """Acceso serializado a la tabla de uso."""

    def __init__(self, connection: aiosqlite.Connection):
        self._connection = connection
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, database_path: Path) -> "UsageRepository":
        """Abre SQLite, crea el esquema e índices y devuelve el repositorio listo."""
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(str(database_path))
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                source TEXT NOT NULL,
                role TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cached_tokens INTEGER NOT NULL DEFAULT 0,
                reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd REAL,
                cost_estimated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_model_usage_chat
                ON model_usage (chat_id, created_at);
            """
        )
        await connection.commit()
        return cls(connection)

    async def add_records(self, chat_id: str, turn_id: str, records: list[UsageRecord]) -> None:
        """Guarda las llamadas de un turno o de un documento en una transacción."""
        if not records:
            return
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        async with self._lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                await self._connection.executemany(
                    """
                    INSERT INTO model_usage (
                        chat_id, turn_id, source, role, model, input_tokens, output_tokens,
                        cached_tokens, reasoning_tokens, cost_usd, cost_estimated, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            chat_id,
                            turn_id,
                            record.source,
                            record.role,
                            record.model,
                            record.input_tokens,
                            record.output_tokens,
                            record.cached_tokens,
                            record.reasoning_tokens,
                            record.cost_usd,
                            int(record.cost_estimated),
                            created_at,
                        )
                        for record in records
                    ],
                )
                await self._connection.commit()
            except Exception:
                await self._connection.rollback()
                raise

    async def summary(self, chat_id: str) -> dict:
        """Resume el uso de una sesión: totales, por rol y modelo, último turno y contexto."""
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT role, model, COUNT(*) AS calls,
                    SUM(input_tokens) AS input_tokens,
                    SUM(output_tokens) AS output_tokens,
                    SUM(cached_tokens) AS cached_tokens,
                    SUM(reasoning_tokens) AS reasoning_tokens,
                    SUM(cost_usd) AS cost_usd,
                    MAX(cost_estimated) AS cost_estimated,
                    MAX(cost_usd IS NULL) AS cost_unknown
                FROM model_usage
                WHERE chat_id = ?
                GROUP BY role, model
                ORDER BY COALESCE(SUM(cost_usd), 0) DESC, role, model
                """,
                (chat_id,),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            cursor = await self._connection.execute(
                """
                SELECT model, input_tokens + output_tokens AS context_used
                FROM model_usage
                WHERE chat_id = ? AND role = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (chat_id, SUPERVISOR_ROLE),
            )
            context = await cursor.fetchone()
            cursor = await self._connection.execute(
                """
                SELECT SUM(cost_usd) AS cost_usd
                FROM model_usage
                WHERE chat_id = ? AND turn_id = (
                    SELECT turn_id FROM model_usage
                    WHERE chat_id = ? AND source = 'turn'
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (chat_id, chat_id),
            )
            last_turn = await cursor.fetchone()

        for row in rows:
            row["cost_estimated"] = bool(row["cost_estimated"])
            row["cost_unknown"] = bool(row["cost_unknown"])
        totals = {field: sum(row[field] for row in rows) for field in TOKEN_FIELDS}
        costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
        return {
            **totals,
            "total_tokens": totals["input_tokens"] + totals["output_tokens"],
            "cost_usd": sum(costs) if costs else None,
            "cost_estimated": any(row["cost_estimated"] for row in rows),
            "cost_unknown": any(row["cost_unknown"] for row in rows),
            "last_turn_cost_usd": last_turn["cost_usd"],
            "context_used": context["context_used"] if context else 0,
            "context_model": context["model"] if context else None,
            "by_model": rows,
        }

    async def delete_chat(self, chat_id: str) -> None:
        """Borra el uso de una sesión."""
        async with self._lock:
            await self._connection.execute("DELETE FROM model_usage WHERE chat_id = ?", (chat_id,))
            await self._connection.commit()

    async def close(self) -> None:
        """Cierra la conexión con SQLite."""
        await self._connection.close()


async def _context_limit(resolver, chat_id: str, model: str | None) -> int | None:
    """Devuelve la ventana de contexto del supervisor; None si no se conoce."""
    if model is None:
        model = (await resolver.resolve(chat_id))[SUPERVISOR_ROLE]["model"]
    try:
        models = await resolver.catalog.list_models()
    except CatalogUnavailable:
        return None
    return next((entry["context_length"] for entry in models if entry["id"] == model), None)


async def session_usage(repository: UsageRepository, resolver, chat_id: str) -> dict:
    """Devuelve el resumen de la sesión con su límite de contexto."""
    summary = await repository.summary(chat_id)
    model = summary.pop("context_model")
    return {**summary, "context_limit": await _context_limit(resolver, chat_id, model)}
