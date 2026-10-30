"""Tool de modelo del alumno: registra dominio y dificultades por tema."""

from typing import Literal

from langchain.tools import ToolRuntime, tool

from core.tools.context import TurnContext


@tool
async def record_progress(
    topic: str,
    status: Literal["dominado", "en_progreso", "con_dificultad"],
    evidence: str,
    runtime: ToolRuntime[TurnContext],
) -> str:
    """Registra el progreso del alumno en un tema del temario tras evaluar una respuesta suya.

    status: dominado (lo demostró), en_progreso (va bien pero falta), con_dificultad
    (falló o dijo no entender). evidence: qué respondió o hizo que lo demuestra, en una frase.
    """
    runtime.context.progress_updates[topic.strip()[:120]] = {
        "status": status,
        "evidence": evidence.strip()[:300],
    }
    return f"Progreso registrado: {topic} → {status}."
