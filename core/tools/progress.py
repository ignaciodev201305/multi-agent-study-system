"""Tool de modelo del alumno: registra dominio y dificultades por tema."""

from typing import Literal

from langchain.tools import ToolRuntime, tool

from core.tools.context import TurnContext


@tool
async def record_progress(
    topic_number: int,
    status: Literal["dominado", "en_progreso", "con_dificultad"],
    evidence: str,
    runtime: ToolRuntime[TurnContext],
) -> str:
    """Registra el progreso del alumno en un tema del temario tras evaluar una respuesta suya.

    topic_number: número del tema en el TEMARIO (empieza en 1). status: dominado (lo demostró),
    en_progreso (va bien pero falta), con_dificultad (falló o dijo no entender).
    evidence: qué respondió o hizo que lo demuestra, en una frase.
    """
    context = runtime.context
    syllabus = context.study_plan.syllabus if context.study_plan else []
    if not 1 <= topic_number <= len(syllabus):
        numbered = "\n".join(f"{index}. {item}" for index, item in enumerate(syllabus, start=1))
        return f"Error: el tema {topic_number} no existe. Temario:\n{numbered}"

    topic = syllabus[topic_number - 1]
    # Conserva solo la última evaluación del tema en el turno.
    context.progress_updates[:] = [
        update for update in context.progress_updates if update["topic_number"] != topic_number
    ]
    context.progress_updates.append({
        "topic_number": topic_number,
        "topic": topic,
        "status": status,
        "evidence": evidence.strip()[:300],
    })
    return f"Progreso registrado: {topic} → {status}."
