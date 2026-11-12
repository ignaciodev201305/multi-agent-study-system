"""Sesiones y transcripción de mensajes en SQLite, independientes del checkpoint del grafo."""

import asyncio
from datetime import datetime
from pathlib import Path

import aiosqlite

from core.study.repository import timestamp

# Largo máximo del título que se deriva del primer mensaje del usuario.
TITLE_MAX_CHARS = 42


class ChatRepository:
    """Acceso serializado a las tablas de sesiones y mensajes."""

    def __init__(self, connection: aiosqlite.Connection):
        self._connection = connection
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, database_path: Path) -> "ChatRepository":
        """Abre SQLite, crea el esquema e índices y devuelve el repositorio listo."""
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(str(database_path))
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS chats (
                chat_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                phase TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                role TEXT NOT NULL,
                agent TEXT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_messages_chat
                ON chat_messages (chat_id, id);
            CREATE INDEX IF NOT EXISTS idx_chats_updated
                ON chats (updated_at);
            """
        )
        await connection.commit()
        return cls(connection)

    async def add_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        now: datetime,
        agent: str | None = None,
        phase: str | None = None,
        title: str | None = None,
    ) -> None:
        """Guarda un mensaje, crea la sesión si no existe y actualiza su fase y título."""
        moment = timestamp(now)
        async with self._lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                await self._connection.execute(
                    """
                    INSERT OR IGNORE INTO chats (chat_id, title, phase, created_at, updated_at)
                    VALUES (?, ?, 'setup', ?, ?)
                    """,
                    (chat_id, content.strip()[:TITLE_MAX_CHARS].strip(), moment, moment),
                )
                await self._connection.execute(
                    """
                    INSERT INTO chat_messages (chat_id, role, agent, content, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (chat_id, role, agent, content, moment),
                )
                await self._connection.execute(
                    """
                    UPDATE chats
                    SET updated_at = ?,
                        phase = COALESCE(?, phase),
                        title = COALESCE(?, title)
                    WHERE chat_id = ?
                    """,
                    (moment, phase, title, chat_id),
                )
                await self._connection.commit()
            except Exception:
                await self._connection.rollback()
                raise

    async def list_chats(self, limit: int) -> list[dict]:
        """Devuelve las sesiones, de la más reciente a la más antigua."""
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT chat_id, title, phase, created_at, updated_at
                FROM chats
                ORDER BY updated_at DESC, chat_id
                LIMIT ?
                """,
                (limit,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def list_messages(self, chat_id: str, limit: int) -> list[dict]:
        """Devuelve los últimos mensajes de una sesión, en orden cronológico."""
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT id, role, agent, content, created_at
                FROM (
                    SELECT id, role, agent, content, created_at
                    FROM chat_messages
                    WHERE chat_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id
                """,
                (chat_id, limit),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def titles(self, chat_ids: list[str]) -> dict[str, str]:
        """Devuelve el título de cada sesión que exista entre los ids pedidos."""
        if not chat_ids:
            return {}
        # Solo se interpolan marcadores "?": los ids viajan como parámetros.
        placeholders = ", ".join("?" for _ in chat_ids)
        async with self._lock:
            cursor = await self._connection.execute(
                f"SELECT chat_id, title FROM chats WHERE chat_id IN ({placeholders})",
                chat_ids,
            )
            return {row["chat_id"]: row["title"] for row in await cursor.fetchall()}

    async def delete_chat(self, chat_id: str) -> None:
        """Borra una sesión y todos sus mensajes."""
        async with self._lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                await self._connection.execute("DELETE FROM chat_messages WHERE chat_id = ?", (chat_id,))
                await self._connection.execute("DELETE FROM chats WHERE chat_id = ?", (chat_id,))
                await self._connection.commit()
            except Exception:
                await self._connection.rollback()
                raise

    async def close(self) -> None:
        """Cierra la conexión con SQLite."""
        await self._connection.close()
