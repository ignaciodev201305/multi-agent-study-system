"""Catálogo SQLite y persistencia de estados para documentos subidos."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


def _utc_timestamp() -> str:
    """Devuelve la hora actual en UTC como texto ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


class DocumentRepository:
    """Acceso serializado al catálogo SQLite de documentos."""

    def __init__(self, connection: aiosqlite.Connection):
        """Guarda la conexión y prepara locks para la BD y cada sesión."""
        self._connection = connection
        self._lock = asyncio.Lock()
        self._chat_locks_guard = asyncio.Lock()
        self._chat_locks: dict[str, dict] = {}

    @classmethod
    async def open(cls, database_path: Path) -> "DocumentRepository":
        """Abre SQLite, crea el esquema e índices y devuelve el repositorio listo."""
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(str(database_path))
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                document_type TEXT NOT NULL CHECK (
                    document_type IN ('rag', 'study_map', 'unknown')
                ),
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('queued', 'processing', 'ready', 'failed')
                ),
                chunks INTEGER NOT NULL DEFAULT 0,
                summary_json TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_documents_chat
                ON documents (chat_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_documents_status
                ON documents (status);
            """
        )
        await cls._add_missing_columns(connection)
        await cls._deduplicate_existing_rows(connection)
        await connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_chat_sha_type
            ON documents (chat_id, sha256, document_type)
            """
        )
        await connection.commit()
        return cls(connection)

    @staticmethod
    async def _add_missing_columns(connection: aiosqlite.Connection) -> None:
        """Migra catálogos creados antes de guardar el resumen por documento."""
        cursor = await connection.execute("PRAGMA table_info(documents)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "summary_json" not in columns:
            await connection.execute("ALTER TABLE documents ADD COLUMN summary_json TEXT")

    @staticmethod
    async def _deduplicate_existing_rows(connection: aiosqlite.Connection) -> None:
        """Preserva un registro por hash/tipo y deja los artefactos sobrantes al cleaner."""
        try:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                """
                SELECT chat_id, sha256, document_type
                FROM documents
                GROUP BY chat_id, sha256, document_type
                HAVING COUNT(*) > 1
                """
            )
            duplicate_groups = await cursor.fetchall()
            for chat_id, sha256, document_type in duplicate_groups:
                cursor = await connection.execute(
                    """
                    SELECT document_id
                    FROM documents
                    WHERE chat_id = ? AND sha256 = ? AND document_type = ?
                    ORDER BY CASE status
                        WHEN 'ready' THEN 0
                        WHEN 'processing' THEN 1
                        WHEN 'queued' THEN 2
                        ELSE 3
                    END, created_at ASC, document_id ASC
                    """,
                    (chat_id, sha256, document_type),
                )
                document_ids = [row[0] for row in await cursor.fetchall()]
                for duplicate_id in document_ids[1:]:
                    await connection.execute(
                        "DELETE FROM documents WHERE document_id = ?",
                        (duplicate_id,),
                    )
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise

    @asynccontextmanager
    async def lock_chat(self, chat_id: str):
        """Evita que el procesamiento y el borrado de una sesión se solapen."""
        async with self._chat_locks_guard:
            entry = self._chat_locks.get(chat_id)
            if entry is None:
                entry = {"lock": asyncio.Lock(), "references": 0}
                self._chat_locks[chat_id] = entry
            entry["references"] += 1
            lock = entry["lock"]

        acquired = False
        try:
            await lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                lock.release()
            async with self._chat_locks_guard:
                entry = self._chat_locks.get(chat_id)
                if entry is not None and entry["lock"] is lock:
                    entry["references"] -= 1
                    if entry["references"] == 0:
                        self._chat_locks.pop(chat_id, None)

    async def reserve_upload(
        self,
        *,
        document_id: str,
        chat_id: str,
        original_filename: str,
        document_type: str,
        size_bytes: int,
        sha256: str,
        max_documents: int,
        max_bytes: int,
    ) -> tuple[str, dict | None]:
        """Deduplica y aplica cuotas antes de insertar el documento como queued."""
        now = _utc_timestamp()
        async with self._lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                cursor = await self._connection.execute(
                    """
                    SELECT * FROM documents
                    WHERE chat_id = ? AND sha256 = ? AND document_type = ?
                    """,
                    (chat_id, sha256, document_type),
                )
                existing = await cursor.fetchone()
                if existing is not None:
                    await self._connection.commit()
                    return "duplicate", dict(existing)

                cursor = await self._connection.execute(
                    """
                    SELECT COUNT(*), COALESCE(SUM(size_bytes), 0)
                    FROM documents WHERE chat_id = ?
                    """,
                    (chat_id,),
                )
                document_count, used_bytes = await cursor.fetchone()
                if document_count >= max_documents:
                    await self._connection.commit()
                    return "document_quota", None
                if used_bytes + size_bytes > max_bytes:
                    await self._connection.commit()
                    return "byte_quota", None

                await self._connection.execute(
                    """
                    INSERT INTO documents (
                        document_id, chat_id, original_filename, document_type,
                        size_bytes, sha256, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        document_id,
                        chat_id,
                        original_filename,
                        document_type,
                        size_bytes,
                        sha256,
                        now,
                        now,
                    ),
                )
                cursor = await self._connection.execute(
                    "SELECT * FROM documents WHERE document_id = ?",
                    (document_id,),
                )
                document = dict(await cursor.fetchone())
                await self._connection.commit()
                return "created", document
            except Exception:
                await self._connection.rollback()
                raise

    async def requeue_failed_duplicate(self, document_id: str) -> dict | None:
        """Reinicia como queued un documento failed para permitir reintentar su subida."""
        async with self._lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                cursor = await self._connection.execute(
                    """
                    UPDATE documents
                    SET status = 'queued', attempts = 0, error = NULL, updated_at = ?
                    WHERE document_id = ? AND status = 'failed'
                    """,
                    (_utc_timestamp(), document_id),
                )
                if cursor.rowcount == 0:
                    await self._connection.commit()
                    return None
                cursor = await self._connection.execute(
                    "SELECT * FROM documents WHERE document_id = ?",
                    (document_id,),
                )
                row = await cursor.fetchone()
                await self._connection.commit()
                return dict(row) if row is not None else None
            except Exception:
                await self._connection.rollback()
                raise

    async def get_document(self, document_id: str) -> dict | None:
        """Busca un documento por ID y devuelve todos sus campos, si existe."""
        async with self._lock:
            cursor = await self._connection.execute(
                "SELECT * FROM documents WHERE document_id = ?",
                (document_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def delete_chat(self, chat_id: str) -> None:
        """Elimina del catálogo todos los documentos asociados a una sesión."""
        async with self._lock:
            await self._connection.execute(
                "DELETE FROM documents WHERE chat_id = ?",
                (chat_id,),
            )
            await self._connection.commit()

    async def list_all(self) -> list[dict]:
        """Devuelve todos los registros para tareas de mantenimiento del catálogo."""
        async with self._lock:
            cursor = await self._connection.execute(
                "SELECT * FROM documents ORDER BY chat_id, created_at"
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def delete_document(self, document_id: str) -> None:
        """Elimina del catálogo el registro identificado por document_id."""
        async with self._lock:
            await self._connection.execute(
                "DELETE FROM documents WHERE document_id = ?",
                (document_id,),
            )
            await self._connection.commit()

    async def claim_queued_document(self, document_id: str) -> dict | None:
        """Cambia queued a processing, suma un intento y devuelve el registro reclamado."""
        async with self._lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                cursor = await self._connection.execute(
                    """
                    UPDATE documents
                    SET status = 'processing', attempts = attempts + 1,
                        error = NULL, updated_at = ?
                    WHERE document_id = ? AND status = 'queued'
                    """,
                    (_utc_timestamp(), document_id),
                )
                if cursor.rowcount == 0:
                    await self._connection.commit()
                    return None
                cursor = await self._connection.execute(
                    "SELECT * FROM documents WHERE document_id = ?",
                    (document_id,),
                )
                row = await cursor.fetchone()
                await self._connection.commit()
                return dict(row) if row is not None else None
            except Exception:
                await self._connection.rollback()
                raise

    async def mark_ready(self, document_id: str, summary: dict, chunks: int) -> bool:
        """Guarda el resultado y marca listo; False si la sesión se borró mientras se procesaba."""
        async with self._lock:
            cursor = await self._connection.execute(
                """
                UPDATE documents
                SET status = 'ready', error = NULL, summary_json = ?, chunks = ?,
                    updated_at = ?
                WHERE document_id = ? AND status = 'processing'
                """,
                (json.dumps(summary, ensure_ascii=False), chunks, _utc_timestamp(), document_id),
            )
            await self._connection.commit()
            return cursor.rowcount > 0

    async def list_ready_with_summaries(self, chat_id: str) -> list[dict]:
        """Documentos listos de una sesión con su resumen, en orden de subida."""
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT document_id, original_filename, document_type, chunks, summary_json
                FROM documents
                WHERE chat_id = ? AND status = 'ready'
                ORDER BY created_at ASC
                """,
                (chat_id,),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
        for row in rows:
            raw = row.pop("summary_json")
            row["summary"] = json.loads(raw) if raw else None
        return rows

    async def mark_failed(self, document_id: str, error: str) -> None:
        """Marca el documento como fallido y guarda el motivo para mostrarlo después."""
        async with self._lock:
            await self._connection.execute(
                """
                UPDATE documents
                SET status = 'failed', error = ?, updated_at = ?
                WHERE document_id = ?
                """,
                (error, _utc_timestamp(), document_id),
            )
            await self._connection.commit()

    async def requeue_document(self, document_id: str, max_attempts: int) -> None:
        """Recupera un trabajo interrumpido o lo falla si agotó sus intentos."""
        async with self._lock:
            await self._connection.execute(
                """
                UPDATE documents
                SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'queued' END,
                    error = CASE WHEN attempts >= ?
                        THEN 'El procesamiento se interrumpió y agotó los intentos.'
                        ELSE NULL END,
                    updated_at = ?
                WHERE document_id = ? AND status = 'processing'
                """,
                (max_attempts, max_attempts, _utc_timestamp(), document_id),
            )
            await self._connection.commit()

    async def recover_jobs(self, max_attempts: int) -> list[str]:
        """Recupera trabajos tras iniciar la app y devuelve los IDs listos para encolar."""
        async with self._lock:
            try:
                await self._connection.execute("BEGIN IMMEDIATE")
                await self._connection.execute(
                    """
                    UPDATE documents
                    SET status = 'failed',
                        error = 'El procesamiento se interrumpió y agotó los intentos.',
                        updated_at = ?
                    WHERE status = 'processing' AND attempts >= ?
                    """,
                    (_utc_timestamp(), max_attempts),
                )
                await self._connection.execute(
                    """
                    UPDATE documents
                    SET status = 'queued', error = NULL, updated_at = ?
                    WHERE status = 'processing' AND attempts < ?
                    """,
                    (_utc_timestamp(), max_attempts),
                )
                cursor = await self._connection.execute(
                    "SELECT document_id FROM documents WHERE status = 'queued' ORDER BY created_at"
                )
                document_ids = [row[0] for row in await cursor.fetchall()]
                await self._connection.commit()
                return document_ids
            except Exception:
                await self._connection.rollback()
                raise

    async def requeue_processing(self, max_attempts: int) -> None:
        """Al apagar, devuelve a cola los documentos que aún figuran processing."""
        async with self._lock:
            await self._connection.execute(
                """
                UPDATE documents
                SET status = CASE WHEN attempts >= ? THEN 'failed' ELSE 'queued' END,
                    error = CASE WHEN attempts >= ?
                        THEN 'El procesamiento se interrumpió y agotó los intentos.'
                        ELSE NULL END,
                    updated_at = ?
                WHERE status = 'processing'
                """,
                (max_attempts, max_attempts, _utc_timestamp()),
            )
            await self._connection.commit()

    async def list_for_chat(self, chat_id: str) -> list[dict]:
        """Lista los documentos de una sesión, del más reciente al más antiguo."""
        async with self._lock:
            cursor = await self._connection.execute(
                """
                SELECT document_id, chat_id, original_filename, document_type,
                       size_bytes, status, chunks, error, created_at, updated_at
                FROM documents
                WHERE chat_id = ?
                ORDER BY created_at DESC
                """,
                (chat_id,),
            )
            return [dict(row) for row in await cursor.fetchall()]

    async def close(self) -> None:
        """Cierra la conexión SQLite del repositorio."""
        await self._connection.close()
