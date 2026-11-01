"""Construcción de cadenas LCEL con salida opcional estructurada."""

import asyncio
import logging

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from openai import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)

# Hay proveedores que aceptan el formato y nunca responden.
STRUCTURED_TIMEOUT_SECONDS = 45
PARSER_TIMEOUT_SECONDS = 60
# Formato no soportado, salida inválida o sin respuesta; los errores de cuota o red fallarían igual.
STRUCTURED_FALLBACK_ERRORS = (BadRequestError, NotFoundError, ValueError, TimeoutError)


def _log_fallback(inputs: dict) -> dict:
    """Registra por qué se usa el parser y quita el error de la entrada."""
    values = dict(inputs)
    error = values.pop("error")
    logger.warning("Salida estructurada no disponible, se usa el parser: %s", type(error).__name__)
    return values


def _with_timeout(chain, seconds: float):
    """Envuelve una cadena para cortarla si supera el tiempo máximo."""

    async def call(inputs, config):
        """Ejecuta la cadena con el tiempo máximo."""
        return await asyncio.wait_for(chain.ainvoke(inputs, config), seconds)

    return RunnableLambda(call)


class ChainBuilder():    
    """Arma cadenas prompt-modelo y añade el parser cuando se define un schema."""
    def build_chain(self, model, prompt, schema=None):
        """Construye la cadena; con schema usa salida Pydantic estructurada."""
        if schema:
            parser_schema = PydanticOutputParser(pydantic_object=schema)
            prompt = ChatPromptTemplate.from_messages([
                ("system", prompt)]).partial(format_instructions=parser_schema.get_format_instructions())
            return prompt | model | parser_schema
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", prompt)])
        return prompt | model

    def build_structured_chain(self, model, prompt, schema, fallback_prompt, native=True):
        """Pide la salida estructurada al proveedor; si falla, usa el parser de JSON."""
        fallback = _with_timeout(self.build_chain(model, fallback_prompt, schema), PARSER_TIMEOUT_SECONDS)
        if not native:
            return fallback
        # Con streaming y response_format, langchain no entrega los tokens de la llamada.
        unstreamed = model.model_copy(update={"disable_streaming": True})
        # Se valida aparte: dentro del SDK, un rechazo dejaría la llamada sin registrar su uso.
        structured = (
            ChatPromptTemplate.from_messages([("system", prompt)])
            | unstreamed.with_structured_output(schema.model_json_schema(), method="json_schema")
            | RunnableLambda(schema.model_validate)
        )
        return _with_timeout(structured, STRUCTURED_TIMEOUT_SECONDS).with_fallbacks(
            [RunnableLambda(_log_fallback) | fallback],
            exceptions_to_handle=STRUCTURED_FALLBACK_ERRORS,
            exception_key="error",
        )
