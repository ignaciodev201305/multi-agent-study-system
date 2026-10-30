"""Creación de modelos LLM y de embeddings a partir del registro de roles."""

import logging

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from config.settings import EMBEDDINGS_MODEL, MODEL_ROLES, OPENAI_API_BASE

logger = logging.getLogger(__name__)


def build_model(role: str, temperature: float | None = None):
    """Construye el ChatOpenAI de un rol de MODEL_ROLES; un arquetipo puede fijar su temperatura."""
    spec = MODEL_ROLES[role]
    kwargs = {
        "model": spec["model"],
        "temperature": spec["temperature"] if temperature is None else temperature,
        "openai_api_base": OPENAI_API_BASE,
    }
    if spec["reasoning_effort"]:
        kwargs["reasoning_effort"] = spec["reasoning_effort"]
    try:
        return ChatOpenAI(**kwargs)
    except Exception:
        logger.exception("Error al instanciar el modelo %s (rol %s)", spec["model"], role)
        return None


def build_embeddings() -> OpenAIEmbeddings:
    """Embeddings del vector store; el modelo se configura con MODEL_EMBEDDINGS."""
    return OpenAIEmbeddings(
        model=EMBEDDINGS_MODEL,
        openai_api_base=OPENAI_API_BASE,
        check_embedding_ctx_length=False,
    )
