"""Prompts y reglas de la sesión de estudio: resumen del historial, formato y documentos."""

# Prompt del resumidor del historial de messages del grafo session_study
SUMMARY_PROMPT = """
    Resumen anterior: {current_summary}
    
    Nuevos mensajes a resumir:
    {messages_to_summarize}
    
    Crea un resumen conciso que capture los puntos clave de la conversación.
   """

# El cliente renderiza la matemática con KaTeX (remark-math + rehype-katex).
MATH_FORMAT_RULE = "Formato: usa LaTeX para la matemática, con $...$ en línea y $$...$$ en bloque."

DOCUMENT_TOOL_RULES = """
DOCUMENTOS Y HERRAMIENTAS:
- Si el contexto de la sesión indica que hay fragmentos consultables (tipo rag),
  usa search_vector_store antes de responder preguntas sobre el contenido de esos PDFs.
- Haz consultas específicas y en lenguaje natural; puedes llamarla más de una vez.
- Cita el documento/fragmento cuando uses información de la tool.
- No inventes contenido que no aparezca en los fragmentos.
- Si la tool no devuelve resultados, dilo y ofrece continuar con el mapa de estudio.
- El mapa de estudio ya está en el contexto; no necesitas buscar para usarlo.
"""
