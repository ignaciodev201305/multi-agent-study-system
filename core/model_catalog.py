"""Catálogo de modelos de OpenRouter: normalización, caché en memoria y filtro por rol."""

import asyncio
import logging
import os
import time

import httpx

from config.settings import OPENAI_API_BASE

logger = logging.getLogger(__name__)

CATALOG_TTL_SECONDS = 3600
# Tras un fallo se sigue usando la copia anterior y se reintenta pronto.
CATALOG_RETRY_SECONDS = 60
CATALOG_TIMEOUT_SECONDS = 15
ROLES_REQUIRING_TOOLS = {"supervisor", "subagent_light", "subagent_heavy", "context_classifier"}


class CatalogUnavailable(Exception):
    """OpenRouter no respondió y no hay una copia anterior del catálogo."""


def _price_per_million(value) -> float | None:
    """Convierte el precio por token a dólares por millón; los routers lo informan negativo."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return round(price * 1_000_000, 6) if price >= 0 else None


def normalize_model(raw: dict) -> dict | None:
    """Reduce un modelo de OpenRouter a los campos que usa la aplicación."""
    model_id = raw.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    # Las variantes por lotes no responden en chat/completions.
    if model_id.endswith(":batch"):
        return None
    parameters = raw.get("supported_parameters") or []
    pricing = raw.get("pricing") or {}
    output_modalities = (raw.get("architecture") or {}).get("output_modalities") or ["text"]
    return {
        "id": model_id,
        "name": raw.get("name") or model_id,
        "context_length": raw.get("context_length"),
        "prompt_price": _price_per_million(pricing.get("prompt")),
        "completion_price": _price_per_million(pricing.get("completion")),
        "supports_tools": "tools" in parameters,
        "supports_reasoning": "reasoning" in parameters,
        "supports_temperature": "temperature" in parameters,
        "supports_text": "text" in output_modalities,
    }


def model_fits_role(model: dict, role: str) -> bool:
    """Indica si un modelo cumple los requisitos de un rol."""
    if not model["supports_text"]:
        return False
    return model["supports_tools"] or role not in ROLES_REQUIRING_TOOLS


def filter_models(models: list[dict], role: str | None = None, query: str = "") -> list[dict]:
    """Filtra los modelos por rol y por texto en su id o nombre."""
    needle = query.strip().lower()
    return [
        model
        for model in models
        if (role is None or model_fits_role(model, role))
        and (needle in model["id"].lower() or needle in model["name"].lower())
    ]


class ModelCatalog:
    """Copia en memoria del catálogo, compartida por toda la aplicación."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport
        self._lock = asyncio.Lock()
        self._models: list[dict] | None = None
        self._expires_at = 0.0

    async def _fetch(self) -> list[dict]:
        """Descarga y normaliza el catálogo."""
        api_key = os.environ.get("OPENAI_API_KEY", "")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        async with httpx.AsyncClient(transport=self._transport, timeout=CATALOG_TIMEOUT_SECONDS) as client:
            response = await client.get(f"{OPENAI_API_BASE}/models", headers=headers)
        response.raise_for_status()
        models = [normalize_model(raw) for raw in response.json()["data"]]
        models = [model for model in models if model is not None]
        if not models:
            raise ValueError("OpenRouter devolvió un catálogo vacío.")
        return models

    async def list_models(self) -> list[dict]:
        """Devuelve el catálogo vigente; si OpenRouter falla, usa la última copia."""
        async with self._lock:
            now = time.monotonic()
            if self._models is not None and now < self._expires_at:
                return self._models
            try:
                self._models = await self._fetch()
                self._expires_at = now + CATALOG_TTL_SECONDS
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
                logger.warning("No se pudo actualizar el catálogo de modelos: %s", type(error).__name__)
                if self._models is None:
                    raise CatalogUnavailable from error
                self._expires_at = now + CATALOG_RETRY_SECONDS
            return self._models
