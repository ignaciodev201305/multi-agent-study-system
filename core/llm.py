"""Creación de modelos LLM y de embeddings a partir del registro de roles."""

import logging

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from config.settings import EMBEDDINGS_MODEL, MODEL_ROLES, OPENAI_API_BASE

logger = logging.getLogger(__name__)


def build_model(role: str, temperature: float | None = None, resolved: dict | None = None):
    """Construye el ChatOpenAI de un rol; `resolved` fija el modelo y sus capacidades."""
    spec = MODEL_ROLES[role]
    resolved = resolved or {}
    model = resolved.get("model", spec["model"])
    kwargs = {"model": model, "openai_api_base": OPENAI_API_BASE}
    # Una capacidad desconocida se manda igual; solo se omite si consta que no existe.
    if resolved.get("supports_temperature") is not False:
        kwargs["temperature"] = spec["temperature"] if temperature is None else temperature
    if spec["reasoning_effort"] and resolved.get("supports_reasoning") is not False:
        kwargs["reasoning_effort"] = spec["reasoning_effort"]
    try:
        return ChatOpenAI(**kwargs)
    except Exception:
        logger.exception("Error al instanciar el modelo %s (rol %s)", model, role)
        return None


def turn_models(config: dict) -> dict | None:
    """Devuelve los modelos resueltos para el turno."""
    return config["configurable"].get("models")


def model_for_turn(config: dict, role: str, fallback):
    """Devuelve el modelo resuelto para el turno, o el de arranque si no hay ninguno."""
    resolved = (turn_models(config) or {}).get(role)
    return build_model(role, resolved=resolved) if resolved else fallback


def build_embeddings() -> OpenAIEmbeddings:
    """Embeddings del vector store; el modelo se configura con MODEL_EMBEDDINGS."""
    return OpenAIEmbeddings(
        model=EMBEDDINGS_MODEL,
        openai_api_base=OPENAI_API_BASE,
        check_embedding_ctx_length=False,
    )
