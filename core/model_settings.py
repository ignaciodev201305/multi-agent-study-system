"""Preferencias de modelos en SQLite y resolución del modelo efectivo de cada rol."""

import asyncio
import logging
import os
from pathlib import Path

import aiosqlite

from config.settings import MODEL_ROLES
from core.model_catalog import CatalogUnavailable, ModelCatalog, model_fits_role

logger = logging.getLogger(__name__)

GLOBAL_SCOPE = "global"


class InvalidModel(ValueError):
    """El modelo no existe en el catálogo o no sirve para el rol."""


class ModelSettingsRepository:
    """Acceso serializado a las preferencias de modelos."""

    def __init__(self, connection: aiosqlite.Connection):
        self._connection = connection
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, database_path: Path) -> "ModelSettingsRepository":
        """Abre SQLite, crea el esquema y devuelve el repositorio listo."""
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(str(database_path))
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA busy_timeout = 5000")
        await connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_settings (
                scope TEXT NOT NULL,
                role TEXT NOT NULL,
                model TEXT NOT NULL,
                PRIMARY KEY (scope, role)
            );
            CREATE TABLE IF NOT EXISTS model_efforts (
                scope TEXT NOT NULL,
                role TEXT NOT NULL,
                effort TEXT NOT NULL,
                PRIMARY KEY (scope, role)
            );
            """
        )
        await connection.commit()
        return cls(connection)

    async def get(self, scope: str) -> dict[str, str]:
        """Devuelve los modelos elegidos en un ámbito, por rol."""
        async with self._lock:
            cursor = await self._connection.execute(
                "SELECT role, model FROM model_settings WHERE scope = ?",
                (scope,),
            )
            return {row["role"]: row["model"] for row in await cursor.fetchall()}

    async def set(self, scope: str, role: str, model: str | None) -> None:
        """Guarda el modelo de un rol; None quita el ajuste."""
        async with self._lock:
            if model is None:
                await self._connection.execute(
                    "DELETE FROM model_settings WHERE scope = ? AND role = ?",
                    (scope, role),
                )
            else:
                await self._connection.execute(
                    """
                    INSERT INTO model_settings (scope, role, model) VALUES (?, ?, ?)
                    ON CONFLICT (scope, role) DO UPDATE SET model = excluded.model
                    """,
                    (scope, role, model),
                )
            await self._connection.commit()

    async def get_efforts(self, scope: str) -> dict[str, str]:
        """Devuelve los efforts elegidos en un ámbito, por rol."""
        async with self._lock:
            cursor = await self._connection.execute(
                "SELECT role, effort FROM model_efforts WHERE scope = ?",
                (scope,),
            )
            return {row["role"]: row["effort"] for row in await cursor.fetchall()}

    async def set_effort(self, scope: str, role: str, effort: str | None) -> None:
        """Guarda el effort de un rol; None quita el ajuste."""
        async with self._lock:
            if effort is None:
                await self._connection.execute(
                    "DELETE FROM model_efforts WHERE scope = ? AND role = ?",
                    (scope, role),
                )
            else:
                await self._connection.execute(
                    """
                    INSERT INTO model_efforts (scope, role, effort) VALUES (?, ?, ?)
                    ON CONFLICT (scope, role) DO UPDATE SET effort = excluded.effort
                    """,
                    (scope, role, effort),
                )
            await self._connection.commit()

    async def delete_chat(self, chat_id: str) -> None:
        """Borra los ajustes de una sesión sin tocar los globales."""
        if chat_id == GLOBAL_SCOPE:
            return
        async with self._lock:
            await self._connection.execute("DELETE FROM model_settings WHERE scope = ?", (chat_id,))
            await self._connection.execute("DELETE FROM model_efforts WHERE scope = ?", (chat_id,))
            await self._connection.commit()

    async def close(self) -> None:
        """Cierra la conexión con SQLite."""
        await self._connection.close()


def _configured_source(role: str) -> str:
    """Indica si el modelo configurado viene del entorno o del valor por defecto."""
    return "env" if os.environ.get(f"MODEL_{role.upper()}") else "default"


def _configured_effort_source(role: str) -> str:
    """Indica si el effort configurado viene del entorno o del valor por defecto."""
    return "env" if os.environ.get(f"REASONING_{role.upper()}") else "default"


def _pick_effort(candidates: list[tuple[str, str | None]], supported: list[str]) -> dict:
    """Elige el primer effort que el modelo admite; sin niveles conocidos respeta lo elegido."""
    unavailable = []
    for source, effort in candidates:
        if effort is None:
            continue
        if not supported or effort in supported:
            return {"effort": effort, "effort_source": source, "effort_unavailable": unavailable}
        unavailable.append(effort)
    return {"effort": None, "effort_source": "model", "effort_unavailable": unavailable}


class ModelResolver:
    """Decide el modelo de cada rol: sesión, global, entorno y valor por defecto."""

    def __init__(self, repository: ModelSettingsRepository, catalog: ModelCatalog):
        self.repository = repository
        self.catalog = catalog

    async def _catalog_by_id(self) -> dict[str, dict] | None:
        """Devuelve el catálogo por id, o None si no se pudo consultar."""
        try:
            return {model["id"]: model for model in await self.catalog.list_models()}
        except CatalogUnavailable:
            return None

    async def resolve(self, chat_id: str | None = None) -> dict[str, dict]:
        """Devuelve el modelo efectivo de cada rol con su origen y capacidades."""
        session = await self.repository.get(chat_id) if chat_id else {}
        global_settings = await self.repository.get(GLOBAL_SCOPE)
        session_efforts = await self.repository.get_efforts(chat_id) if chat_id else {}
        global_efforts = await self.repository.get_efforts(GLOBAL_SCOPE)
        catalog = await self._catalog_by_id()

        resolved = {}
        for role, spec in MODEL_ROLES.items():
            candidates = [("session", session.get(role)), ("global", global_settings.get(role))]
            unavailable = []
            model, source = spec["model"], _configured_source(role)
            for candidate_source, candidate in candidates:
                if candidate is None:
                    continue
                # Sin catálogo no se puede comprobar: se respeta lo elegido.
                if catalog is None or (candidate in catalog and model_fits_role(catalog[candidate], role)):
                    model, source = candidate, candidate_source
                    break
                logger.warning("Modelo guardado no disponible role=%s model=%s", role, candidate)
                unavailable.append(candidate)

            details = (catalog or {}).get(model, {})
            supported_efforts = details.get("supported_efforts") or []
            effort = _pick_effort(
                [
                    ("session", session_efforts.get(role)),
                    ("global", global_efforts.get(role)),
                    (_configured_effort_source(role), spec["reasoning_effort"]),
                ],
                supported_efforts,
            )
            resolved[role] = {
                "model": model,
                "source": source,
                "unavailable": unavailable,
                "supports_temperature": details.get("supports_temperature"),
                "supports_reasoning": details.get("supports_reasoning"),
                "supports_structured_output": details.get("supports_structured_output"),
                "supported_efforts": supported_efforts,
                "default_effort": details.get("default_effort"),
                **effort,
            }
        return resolved

    async def check_effort(self, chat_id: str | None, role: str, effort: str) -> None:
        """Rechaza un effort que el modelo efectivo del rol no admita."""
        catalog = {item["id"]: item for item in await self.catalog.list_models()}
        model = (await self.resolve(chat_id))[role]["model"]
        if effort not in (catalog.get(model) or {}).get("supported_efforts", []):
            raise InvalidModel("El modelo no admite ese nivel de razonamiento.")

    async def check_model(self, role: str, model: str) -> None:
        """Rechaza un modelo que no exista o no cumpla los requisitos del rol."""
        catalog = {item["id"]: item for item in await self.catalog.list_models()}
        if model not in catalog:
            raise InvalidModel("El modelo no existe en el catálogo.")
        if not model_fits_role(catalog[model], role):
            raise InvalidModel("El modelo no cumple los requisitos de este rol.")
