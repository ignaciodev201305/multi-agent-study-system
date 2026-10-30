"""Contexto de un turno que reciben las tools vía ToolRuntime (no lo elige el modelo)."""

from dataclasses import dataclass, field


@dataclass
class TurnContext:
    # La sesión viene del servidor: una tool nunca puede consultar otra sesión.
    session_id: str
    # Documentos ready de la sesión con su resumen (ver DocumentRepository.list_ready_with_summaries).
    documents: list[dict] = field(default_factory=list)
    # record_progress escribe aquí; el nodo chat lo guarda en el estado de la sesión.
    progress_updates: dict[str, dict] = field(default_factory=dict)
