"""Middleware de los agentes: tope de llamadas al modelo por turno y caché de prompts."""

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain.messages import AIMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

CACHE_CONTROL = {"type": "ephemeral"}
# OpenRouter solo cachea los modelos de Anthropic si el pedido marca dónde cortar; el resto cachea solo.
CACHED_MODEL_PREFIXES = ("anthropic/",)


def calls_this_turn(messages: list) -> int:
    """Cuenta las respuestas del modelo desde el último mensaje del alumno o del profesor."""
    count = 0
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage):
            count += 1
    return count


class CallLimitMiddleware(AgentMiddleware):
    """Acota las llamadas al modelo por invocación; la última va sin tools para cerrar con una respuesta."""

    def __init__(self, max_calls: int, limit_message: str, agent_name: str):
        super().__init__()
        self.max_calls = max_calls
        self.limit_message = limit_message
        self.agent_name = agent_name

    def _prepare(self, request):
        """Devuelve el pedido a ejecutar, o None si ya no quedan llamadas."""
        calls = calls_this_turn(request.messages)
        if calls >= self.max_calls:
            # Solo ocurre si el proveedor ignoró tool_choice="none" en la llamada anterior.
            logger.warning("%s agotó %s llamadas al modelo en el turno", self.agent_name, self.max_calls)
            return None
        if calls == self.max_calls - 1 and request.tools:
            logger.warning("%s llegó a su última llamada del turno: responde sin tools", self.agent_name)
            return request.override(tool_choice="none")
        return request

    def wrap_model_call(self, request, handler):
        prepared = self._prepare(request)
        return AIMessage(content=self.limit_message) if prepared is None else handler(prepared)

    async def awrap_model_call(self, request, handler):
        prepared = self._prepare(request)
        return AIMessage(content=self.limit_message) if prepared is None else await handler(prepared)


def with_cache_point(message: SystemMessage) -> SystemMessage:
    """Copia el mensaje con un punto de corte de caché al final de su contenido."""
    content = message.content
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else [
        dict(block) if isinstance(block, dict) else {"type": "text", "text": block} for block in content
    ]
    if not blocks:
        return message
    blocks[-1] = {**blocks[-1], "cache_control": CACHE_CONTROL}
    return message.model_copy(update={"content": blocks})


class PromptCacheMiddleware(AgentMiddleware):
    """Marca el prompt fijo y el contexto del turno para que OpenRouter los cachee en modelos de Anthropic."""

    def _prepare(self, request):
        model_name = getattr(request.model, "model_name", "") or ""
        if not model_name.startswith(CACHED_MODEL_PREFIXES):
            return request
        overrides = {}
        # Las tools van antes del prompt: este corte cachea ambos entre turnos.
        if request.system_message is not None:
            overrides["system_message"] = with_cache_point(request.system_message)
        # El contexto del turno cambia entre turnos, pero no entre las llamadas de un mismo turno.
        messages = request.messages
        if messages and isinstance(messages[0], SystemMessage):
            overrides["messages"] = [with_cache_point(messages[0]), *messages[1:]]
        return request.override(**overrides) if overrides else request

    def wrap_model_call(self, request, handler):
        return handler(self._prepare(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._prepare(request))
