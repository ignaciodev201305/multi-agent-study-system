"""Catálogo de arquetipos de subagente por función y técnicas pedagógicas reutilizables."""

from dataclasses import dataclass
from typing import get_args

from core.schemas import ArchetypeName, AreaName

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

BOARD_RULE = (
    "PIZARRA: usa board_add solo para lo que el alumno consultará después (fórmulas, pasos, "
    "tablas y gráficos). No dupliques tu respuesta ni escribas mensajes conversacionales en ella. "
    "Para citar un bloque, escribe su id entre corchetes dobles, como [[b3]]."
)


@dataclass(frozen=True)
class Archetype:
    """Especialista del catálogo con su prompt, tools, modelo y técnicas."""
    name: str
    # La leen el factory y el supervisor.
    description: str
    prompt: str
    tools: tuple[str, ...]
    # Valores "light" o "heavy".
    tier: str
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
            tools=("math", "search_documents", "get_study_material", "board_add"),
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
                "siempre los resultados con la tool math antes de darlos por buenos. Pon en la "
                "pizarra los ejercicios para practicar como bloques exercise, con pistas graduadas "
                "y solución verificada, y en tu respuesta cita el bloque sin revelar la solución."
            ),
            tools=("math", "get_study_material", "search_documents", "board_add"),
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
            tools=("math", "get_study_material", "search_documents", "board_add"),
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
            tools=("search_documents", "get_study_material", "board_add"),
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
        Archetype(
            name="tutor_de_codigo",
            description="Enseña a programar en Python y ayuda a depurar el código que el alumno ejecuta.",
            prompt=(
                "Eres un tutor de programación en Python. Explicas con ejemplos cortos, lees el "
                "código y la salida que comparte el alumno y señalas la línea exacta del error. "
                "El código corre en el navegador del alumno cuando él lo decide, con numpy, sympy "
                "y matplotlib disponibles, pero sin archivos ni red fiables. Pon los ejemplos "
                "ejecutables en la pizarra como bloques code con language python, y nunca des "
                "por hecho un resultado que el alumno no haya compartido."
            ),
            tools=("board_add", "math"),
            tier="heavy",
            temperature=0.2,
            techniques=("paso_a_paso", "reexplicar"),
        ),
    )
}

assert set(ARCHETYPES) == set(get_args(ArchetypeName)), "El catálogo y ArchetypeName deben coincidir"


@dataclass(frozen=True)
class Area:
    """Perfil de área: tools de cálculo que se suman a la sesión y una nota para los prompts."""
    name: str
    # Nombre que ve el alumno.
    label: str
    # La lee el factory.
    description: str
    tools: tuple[str, ...]
    note: str


AREAS = {
    area.name: area
    for area in (
        Area(
            name="informatica",
            label="Informática",
            description="Lógica digital y proposicional, álgebra de Boole, circuitos combinacionales.",
            tools=("logic",),
            note=(
                "Lógica: verifica con la tool logic toda tabla de verdad, simplificación o "
                "equivalencia antes de darla o de juzgar la del alumno."
            ),
        ),
    )
}

assert set(AREAS) == set(get_args(AreaName)), "Las áreas y AreaName deben coincidir"


def areas_for_factory() -> str:
    """Listado que lee el factory para elegir áreas."""
    return "\n".join(f"- {area.name}: {area.description}" for area in AREAS.values())


def area_tools(names) -> tuple[str, ...]:
    """Tools de cálculo de las áreas elegidas, sin repetir."""
    return tuple(dict.fromkeys(tool for name in names for tool in AREAS[name].tools))


def area_notes(names) -> str:
    """Notas de prompt de las áreas elegidas; vacío si no hay ninguna."""
    notes = "\n".join(f"- {AREAS[name].note}" for name in names)
    return f"ÁREA DE LA SESIÓN:\n{notes}" if notes else ""


def catalog_for_factory() -> str:
    """Listado que lee el factory para elegir arquetipos."""
    return "\n".join(
        f"- {archetype.name}: {archetype.description}" for archetype in ARCHETYPES.values()
    )


def archetype_prompt(archetype: Archetype, focus: str) -> str:
    """Prompt completo del subagente: base + técnicas + foco de la sesión."""
    techniques = "\n".join(f"- {TECHNIQUES[name]}" for name in archetype.techniques)
    board_rule = f"{BOARD_RULE}\n\n" if "board_add" in archetype.tools else ""
    return (
        f"{archetype.prompt}\n\n"
        f"TÉCNICAS:\n{techniques}\n\n"
        f"FOCO EN ESTA SESIÓN:\n{focus}\n\n"
        f"{board_rule}"
        "Te consulta el profesor principal: responde a su pedido con la explicación o el "
        "ejercicio listo para mostrar al alumno, en segunda persona dirigida al alumno."
    )
