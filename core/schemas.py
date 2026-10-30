"""Modelos Pydantic para el sistema multiagente y los contextos de estudio."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

class SubAgent(BaseModel):
    """Subagente especialista con su rol, prompt y nivel de complejidad."""
    specialization: str = Field(description="Rol del subagente. FORMATO: [a-zA-Z0-9_-]")
    prompt: str = Field(description="System prompt del tutor, en segunda persona. MAX 400 PALABRAS", max_length=3000)
    complexity_role: bool = Field(default=False, description="True solo si el tutor exige razonamiento multi-paso, abstracción o demostración (ej. regla de la cadena); False si es explicación directa o práctica guiada. Ante la duda, False.")

class SupervisorAgent(BaseModel):
    """Supervisor que coordina a los subagentes durante la sesión."""
    name: str = Field(description="Rol del supervisor. FORMATO: [a-zA-Z0-9_-]")
    prompt: str = Field(description="System prompt del supervisor, en segunda persona. MAX 400 PALABRAS", max_length=3000)


class MultiAgentSystem(BaseModel):
    """Configuración completa del supervisor y sus subagentes."""
    supervisor: SupervisorAgent
    subagents: List[SubAgent] = Field(description="Lista de subagentes.")

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
