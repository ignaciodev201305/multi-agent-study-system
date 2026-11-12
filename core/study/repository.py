"""Progreso por tema e historial de revisiones en SQLite."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

import aiosqlite

from core.study.scheduler import next_review

# Revisiones que se devuelven por tema.
REVIEWS_PER_TOPIC = 3


class ProgressUpdate(TypedDict):
    """Evaluación de un tema durante un turno."""
    topic_number: int
    topic: str
    status: str
    evidence: str


def timestamp(moment: datetime) -> str:
    """Normaliza la fecha a UTC para que el texto se pueda ordenar."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


class ProgressRepository:
    """Acceso serializado a las tablas de progreso."""

    def __init__(self, connection: aiosqlite.Connection):
        self._connection = connection
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, database_path: Path) -> "ProgressRepository":
        """Abre SQLite, crea el esquema e índices y devuelve el repositorio listo."""
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(str(database_path))
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS topic_progress (
                chat_id TEXT NOT NULL,
                topic_number INTEGER NOT NULL,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                box INTEGER NOT NULL,
                due_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, topic_number)
            );
            CREATE TABLE IF NOT EXISTS topic_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                topic_number INTEGER NOT NULL,
                status TEXT NOT NULL,
                evidence TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_topic_progress_due
                ON topic_progress (due_at);
            CREATE INDEX IF NOT EXISTS idx_topic_reviews_chat
                ON topic_reviews (chat_id, created_at);
            """
        )
        await connection.commit()
        return cls(connection)

    async def apply_updates(self, chat_id: str, updates: list[ProgressUpdate], now: datetime) -> None:
        """Guarda las evaluaciones de un turno y reprograma sus temas en una transacción."""
        if not updates:
            return
        async with self._lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                for update in updates:
                    await self._apply_update(chat_id, update, now)
                await self._connection.commit()
            except Exception:
                await self._connection.rollback()
                raise

    async def _apply_update(self, chat_id: str, update: ProgressUpdate, now: datetime) -> None:
        """Actualiza un tema y agrega su revisión al historial."""
        cursor = await self._connection.execute(
            "SELECT box FROM topic_progress WHERE chat_id = ? AND topic_number = ?",
            (chat_id, update["topic_number"]),
        )
        current = await cursor.fetchone()
        box, due_at = next_review(current["box"] if current else 0, update["status"], now)
        await self._connection.execute(
            """
            INSERT INTO topic_progress (chat_id, topic_number, topic, status, box, due_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (chat_id, topic_number) DO UPDATE SET
                topic = excluded.topic,
                status = excluded.status,
                box = excluded.box,
                due_at = excluded.due_at,
                updated_at = excluded.updated_at
            """,
            (
                chat_id,
                update["topic_number"],
                update["topic"],
                update["status"],
                box,
                timestamp(due_at),
                timestamp(now),
            ),
        )
        await self._connection.execute(
            """
            INSERT INTO topic_reviews (chat_id, topic_number, status, evidence, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, update["topic_number"], update["status"], update["evidence"], timestamp(now)),
        )

    async def list_for_chat(self, chat_id: str) -> list[dict]:
        """Devuelve los temas de una sesión con sus revisiones más recientes."""
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT topic_number, topic, status, box, due_at, updated_at
                FROM topic_progress
                WHERE chat_id = ?
                ORDER BY topic_number
                """,
                (chat_id,),
            )
            topics = [{**dict(row), "reviews": []} for row in await cursor.fetchall()]
            cursor = await self._connection.execute(
                """
                SELECT topic_number, status, evidence, created_at
                FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY topic_number ORDER BY id DESC
                    ) AS position
                    FROM topic_reviews
                    WHERE chat_id = ?
                )
                WHERE position <= ?
                ORDER BY position
                """,
                (chat_id, REVIEWS_PER_TOPIC),
            )
            reviews = await cursor.fetchall()

        by_number = {topic["topic_number"]: topic for topic in topics}
        for review in reviews:
            topic = by_number.get(review["topic_number"])
            if topic is not None:
                topic["reviews"].append({
                    "status": review["status"],
                    "evidence": review["evidence"],
                    "created_at": review["created_at"],
                })
        return topics

    async def list_due(self, now: datetime, limit: int) -> list[dict]:
        """Devuelve los repasos vencidos de todas las sesiones, del más antiguo al más nuevo."""
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT chat_id, topic_number, topic, status, due_at
                FROM topic_progress
                WHERE due_at <= ?
                ORDER BY due_at, chat_id, topic_number
                LIMIT ?
                """,
                (timestamp(now), limit),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def delete_chat(self, chat_id: str) -> None:
        """Borra el progreso y el historial de una sesión."""
        async with self._lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                await self._connection.execute("DELETE FROM topic_progress WHERE chat_id = ?", (chat_id,))
                await self._connection.execute("DELETE FROM topic_reviews WHERE chat_id = ?", (chat_id,))
                await self._connection.commit()
            except Exception:
                await self._connection.rollback()
                raise

    async def close(self) -> None:
        """Cierra la conexión con SQLite."""
        await self._connection.close()
