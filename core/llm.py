"""Creación de modelos LLM y de embeddings a partir del registro de roles."""

import logging

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from config.settings import EMBEDDINGS_MODEL, MODEL_ROLES, OPENAI_API_BASE

logger = logging.getLogger(__name__)

# Etiquetas con las que el registro de uso identifica cada llamada.
ROLE_TAG = "role:"
MODEL_TAG = "model:"


def _chunk_cost(chunk) -> float | None:
    """Devuelve el costo que informa un chunk, o None si no trae uno válido."""
    try:
        cost = chunk["usage"]["cost"]
    except (KeyError, TypeError):
        return None
    return cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None


class OpenRouterChat(ChatOpenAI):
    """ChatOpenAI que conserva el costo que OpenRouter informa en cada respuesta."""

    def _convert_chunk_to_generation_chunk(self, chunk, *args, **kwargs):
        """Copia el costo del chunk de uso a los metadatos del mensaje."""
        generation = super()._convert_chunk_to_generation_chunk(chunk, *args, **kwargs)
        cost = _chunk_cost(chunk)
        if generation is not None and cost is not None:
            generation.message.response_metadata["cost"] = cost
        return generation


def build_model(role: str, temperature: float | None = None, resolved: dict | None = None):
    """Construye el modelo de un rol; `resolved` fija el modelo y sus capacidades."""
    spec = MODEL_ROLES[role]
    resolved = resolved or {}
    model = resolved.get("model", spec["model"])
    # OpenRouter puede responder con otro id: la etiqueta conserva el que está en el catálogo.
    kwargs = {
        "model": model,
        "openai_api_base": OPENAI_API_BASE,
        "tags": [f"{ROLE_TAG}{role}", f"{MODEL_TAG}{model}"],
    }
    # Una capacidad desconocida se manda igual; solo se omite si consta que no existe.
    if resolved.get("supports_temperature") is not False:
        kwargs["temperature"] = spec["temperature"] if temperature is None else temperature
    if spec["reasoning_effort"] and resolved.get("supports_reasoning") is not False:
        kwargs["reasoning_effort"] = spec["reasoning_effort"]
    try:
        return OpenRouterChat(**kwargs)
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
