"""Creación de las instancias de modelos LLM que usan los agentes y grafos."""

import logging

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from config.settings import OPENAI_API_BASE

load_dotenv()

logger = logging.getLogger(__name__)


class BuildModel():
    """Construye modelos ChatOpenAI apuntando al endpoint configurado."""
    def build_model(self,  model: str, temperature: float, reasoning_effort: str = None ):
        try:
            if not reasoning_effort:
                return ChatOpenAI(model=model, temperature=temperature, openai_api_base=OPENAI_API_BASE)
            return ChatOpenAI(model=model, temperature=temperature, reasoning_effort=reasoning_effort, openai_api_base=OPENAI_API_BASE)
        except Exception:
            logger.exception("Error al instanciar el modelo %s", model)
            return None
