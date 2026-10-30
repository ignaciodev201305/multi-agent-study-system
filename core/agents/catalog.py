"""Catálogo de arquetipos de subagente por función y técnicas pedagógicas reutilizables.

El factory no escribe prompts: elige arquetipos de aquí y redacta solo un `focus` corto
para cada uno. Cada arquetipo fija su prompt base, sus tools, su tier de modelo y su
temperatura; las técnicas son bloques de texto que el arquetipo incluye.
"""

from dataclasses import dataclass
from typing import get_args

from core.schemas import ArchetypeName

TECHNIQUES = {
    "socratico": (
        "Método socrático: antes de explicar, pregunta qué sabe o qué intentaría el alumno. "
        "Guía con preguntas cortas hacia la idea en vez de darla hecha; explica directamente "
        "solo si tras dos preguntas sigue bloqueado."
    ),
    "paso_a_paso": (
        "Demostración paso a paso: un paso por línea, diciendo qué regla o propiedad se aplica "
        "en cada uno. Usa un ejemplo distinto en cada explicación y cierra con UNA pregunta de "
        "comprobación corta sobre lo recién explicado."
    ),
    "reexplicar": (
        "Si el alumno dice 'no sé' o 'no entiendo', o responde mal: reexplica con un enfoque "
        "distinto al anterior (analogía, ejemplo cotidiano, pasos más pequeños) y haz otra "
        "pregunta distinta. Nunca repitas la misma explicación ni la misma pregunta."
    ),
    "simulacro": (
        "Simulacro de examen: plantea preguntas con el formato y la dificultad del examen "
        "objetivo, de una en una, sin pistas. Corrige al final de cada respuesta indicando el "
        "error concreto y la forma correcta."
    ),
    "repeticion_espaciada": (
        "Repetición espaciada: vuelve sobre los temas marcados con dificultad en el progreso "
        "del alumno con preguntas nuevas, mezcladas con lo actual."
    ),
    "informe_con_fuentes": (
        "Informe con fuentes: separa hechos de interpretación, cita cada afirmación con su "
        "fuente (documento y página, o URL) y señala lo que no pudiste verificar."
    ),
}


@dataclass(frozen=True)
class Archetype:
    name: str
    description: str  # Lo lee el factory para elegir y el supervisor como descripción de la tool.
    prompt: str
    tools: tuple[str, ...]
    tier: str  # "light" o "heavy": rol subagent_light / subagent_heavy de MODEL_ROLES.
    temperature: float
    techniques: tuple[str, ...]


ARCHETYPES = {
    archetype.name: archetype
    for archetype in (
        Archetype(
            name="explicador",
            description="Explica conceptos y procedimientos nuevos desde la base, con ejemplos.",
            prompt=(
                "Eres un tutor experto en explicar. Enseñas conceptos y procedimientos ajustándote "
                "al nivel del alumno, anticipas los errores frecuentes del tema y compruebas la "
                "comprensión antes de avanzar."
            ),
            tools=("math", "search_documents", "get_study_material"),
            tier="light",
            temperature=0.3,
            techniques=("paso_a_paso", "reexplicar"),
        ),
        Archetype(
            name="entrenador_de_ejercicios",
            description="Guía la práctica: propone ejercicios graduados y corrige el razonamiento paso a paso.",
            prompt=(
                "Eres un entrenador de ejercicios. Propones ejercicios de dificultad creciente "
                "(primero los del material del alumno si existen), dejas que el alumno los intente "
                "y corriges su razonamiento señalando el paso exacto donde se equivoca. Verifica "
                "siempre los resultados con la tool math antes de darlos por buenos."
            ),
            tools=("math", "get_study_material", "search_documents"),
            tier="heavy",
            temperature=0.2,
            techniques=("socratico", "reexplicar"),
        ),
        Archetype(
            name="evaluador",
            description="Evalúa con quizzes o simulacros tipo examen y detecta qué temas faltan.",
            prompt=(
                "Eres un evaluador. Diseñas preguntas de comprobación o simulacros alineados con el "
                "objetivo del alumno, corriges con criterio explícito y resumes qué temas domina y "
                "en cuáles tiene dificultad."
            ),
            tools=("math", "get_study_material"),
            tier="light",
            temperature=0.2,
            techniques=("simulacro", "repeticion_espaciada"),
        ),
        Archetype(
            name="analista_de_documentos",
            description="Responde con el contenido de los PDFs subidos por el alumno, citando documento y página.",
            prompt=(
                "Eres un analista de documentos. Respondes únicamente con lo que dicen los PDFs del "
                "alumno: buscas los fragmentos relevantes, los explicas y citas documento y página. "
                "Si los documentos no lo cubren, lo dices sin inventar."
            ),
            tools=("search_documents", "get_study_material"),
            tier="light",
            temperature=0.0,
            techniques=("informe_con_fuentes",),
        ),
        Archetype(
            name="investigador",
            description="Investiga en la web y en los PDFs del alumno y sintetiza con citas.",
            prompt=(
                "Eres un investigador. Descompones la pregunta en sub-preguntas, buscas en la web y "
                "en los PDFs del alumno, contrastas las fuentes y sintetizas una respuesta clara con "
                "citas. Prioriza fuentes primarias y recientes."
            ),
            tools=("web_search", "search_documents"),
            tier="heavy",
            temperature=0.2,
            techniques=("informe_con_fuentes",),
        ),
    )
}

assert set(ARCHETYPES) == set(get_args(ArchetypeName)), "El catálogo y ArchetypeName deben coincidir"


def catalog_for_factory() -> str:
    """Listado que lee el factory para elegir arquetipos."""
    return "\n".join(
        f"- {archetype.name}: {archetype.description}" for archetype in ARCHETYPES.values()
    )


def archetype_prompt(archetype: Archetype, focus: str) -> str:
    """Prompt completo del subagente: base + técnicas + foco de la sesión."""
    techniques = "\n".join(f"- {TECHNIQUES[name]}" for name in archetype.techniques)
    return (
        f"{archetype.prompt}\n\n"
        f"TÉCNICAS:\n{techniques}\n\n"
        f"FOCO EN ESTA SESIÓN:\n{focus}\n\n"
        "Te consulta el profesor principal: responde a su pedido con la explicación o el "
        "ejercicio listo para mostrar al alumno, en segunda persona dirigida al alumno."
    )
