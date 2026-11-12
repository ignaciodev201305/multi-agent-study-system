"""Modelos Pydantic del plan de estudio y de los contextos de la sesión."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

ArchetypeName = Literal[
    "explicador",
    "entrenador_de_ejercicios",
    "evaluador",
    "analista_de_documentos",
    "investigador",
    "tutor_de_codigo",
]

AreaName = Literal["informatica", "datos_ia", "mecanica_civil"]


class SelectedArchetype(BaseModel):
    """Arquetipo del catálogo elegido para la sesión, con su foco concreto."""
    archetype: ArchetypeName = Field(description="Nombre exacto del arquetipo del catálogo.")
    focus: str = Field(
        description="Tema, alcance y nivel con que este arquetipo trabaja en la sesión. MAX 80 PALABRAS",
        max_length=800,
    )


class StudyPlan(BaseModel):
    """Plan de la sesión: temario que guarda el supervisor y arquetipos que lo apoyan."""
    subject: str = Field(description="Tema de la sesión en pocas palabras (ej. 'regla de la cadena').")
    syllabus: List[str] = Field(
        description="Temario ordenado de menor a mayor dependencia, de 2 a 8 puntos.",
        min_length=1,
        max_length=12,
    )
    supervisor_focus: str = Field(
        description="Cómo debe conducir la sesión el profesor principal (nivel, objetivo, ritmo). MAX 120 PALABRAS",
        max_length=1200,
    )
    archetypes: List[SelectedArchetype] = Field(
        description="Entre 1 y 4 arquetipos distintos del catálogo.",
        min_length=1,
        max_length=4,
    )
    areas: List[AreaName] = Field(
        default_factory=list,
        description="De 0 a 2 áreas del catálogo; vacío si el tema no pertenece claramente a ninguna.",
        max_length=2,
    )

    @field_validator("areas")
    @classmethod
    def _unique_areas(cls, areas):
        """Descarta áreas repetidas conservando el orden."""
        return list(dict.fromkeys(areas))

    @field_validator("archetypes")
    @classmethod
    def _unique_archetypes(cls, archetypes):
        """Rechaza arquetipos repetidos en el plan."""
        names = [selected.archetype for selected in archetypes]
        if len(names) != len(set(names)):
            raise ValueError("Cada arquetipo puede elegirse una sola vez.")
        return archetypes


class ContextClassification(BaseModel):
    approved_context: bool = Field(description="Bandera que indica si apruebas el contexto proporcionado por el usuario")
    questions: List[str] | None = Field(default=[], description="Preguntas en caso que el contexto sea insufiente. Solo si approved_context es False. MAXIMO 3 PREGUNTAS") 
    session_objetive: str | None = Field(default=None, description="Indicar el objetivo de la sesion de estudio(Debe ser proporcionada por el usuario, puedes inferir, pero debes consultarle al usuario). Solo si approved_context es True")
    session_context: str | None = Field(default=None, description="Ordenar el contexto de la sesion sin alterar la intencion del usuario. Solo si approved_context es True")
    previous_level: Literal["principiante", "intermedio", "avanzado"] | None = Field(default=None, description="Nivel previo del usuario. No inferir.")
    title_session: str | None = Field(default=None, description="Titulo de la sesion de acuerdo al contexto(Solo cuando haya contexto claro). MAXIMO 30 PALABRAS")

class MapStudy(BaseModel):
    title: str = Field(description="Titulo breve del mapa de estudio.")
    summary: str = Field(description="Resumen breve(2 parrafos maximo) del pdf")
    topics:  List[str] = Field(description="Temas importantes")
    formulas: Optional[List[str]] = Field(default=None, description="Formulas en caso de que las haya")
    exercises: Optional[List[str]] = Field(default=None, description="Ejercicios en caso de que hayan")
    concepts: Optional[List[str]] = Field(default=None, description="Concepto claves")

class RagContext(BaseModel):
    """Resumen global de un PDF indexado para el contexto RAG."""
    title: str = Field(description="Titulo breve del pdf.")
    summary: str = Field(description="Resumen de maximo 300 lineas que explica el PDF")
