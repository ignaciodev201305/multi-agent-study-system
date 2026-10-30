"""Prompts del procesamiento de documentos: mapa de estudio, contexto RAG y map-reduce."""

MAP_STUDY_PROMPT = """Eres un asistente experto en educación y diseño de material de estudio. Tu tarea es analizar el contenido de un documento PDF y generar un mapa de estudio claro, fiel y útil para un estudiante.

## DOCUMENTO
El contenido fue extraído con Docling y puede incluir encabezados, tablas, listas y fórmulas en formato texto/markdown:

<documento>
{documents}
</documento>

## INSTRUCCIONES

{format_instructions}

Genera la salida con los siguientes campos:

1. **summary**: Resumen breve del documento, de máximo 2 párrafos. Explica de qué trata, cuál es su objetivo y las ideas principales. No incluyas detalles menores ni copies texto literal.

2. **topics**: Lista de los temas importantes del documento, en el orden en que aparecen o en un orden lógico de estudio. Cada tema debe ser una frase corta y específica (ej: "Derivadas de funciones compuestas", no solo "Derivadas").

3. **formulas**: Lista de las fórmulas, ecuaciones o expresiones matemáticas/científicas relevantes que aparezcan en el documento. Escribe cada una en LaTeX entre $...$ y, si ayuda, agrega una breve aclaración de qué representa o qué significa cada variable. Si el documento no contiene fórmulas, devuelve null.

4. **exercises**: Lista de los ejercicios, problemas o actividades propuestas que aparezcan en el documento. Incluye el enunciado resumido de cada uno. No los resuelvas ni inventes ejercicios nuevos. Si no hay ejercicios, devuelve null.

5. **concepts**: Lista de los conceptos clave, definiciones o términos técnicos que el estudiante debe dominar. Cada elemento debe tener el formato "Término: definición breve". Si no hay conceptos claros, devuelve null.

## REGLAS
- Basa tu respuesta ÚNICAMENTE en el contenido del documento. No inventes ni agregues información externa.
- Responde en el mismo idioma del documento.
- Sé conciso: prioriza lo esencial sobre lo exhaustivo y evita elementos repetidos.
- Ignora ruido de la extracción (números de página, encabezados o pies repetidos, texto cortado, referencias a imágenes).
- Si un campo opcional no aplica al documento, devuélvelo como null en lugar de inventar contenido.
"""

BUILD_RAG_CONTEXT_PROMPT = """
Genera un contexto global y conciso del contenido de los siguientes documentos para su uso en un sistema RAG.

Documentos:
{documents}

Instrucciones:
- Identifica el tema principal y la información más relevante.
- Sintetiza los conceptos, hechos, definiciones y relaciones necesarios para comprender el contenido.
- Conserva nombres propios, fechas, cifras y términos técnicos importantes.
- Si hay varios documentos, integra su información sin confundir sus contenidos.
- Elimina redundancias, detalles secundarios y contenido que no aporte valor.
- No agregues información externa, interpretaciones innecesarias ni conclusiones que no estén respaldadas por los documentos.
- Prioriza la densidad informativa sobre la extensión. No intentes cubrir cada detalle.
- Usa títulos breves y párrafos solo cuando mejoren la comprensión.

{format_instructions}

Devuelve únicamente el contexto final, sin introducciones ni explicaciones adicionales.
Mantén la respuesta lo más breve posible sin perder la información esencial.
"""

PARTIAL_SUMMARY_PROMPT = """Resume la parte {index} de {total} de un documento de estudio.

<documento>
{block}
</documento>

- Conserva temas, definiciones, fórmulas, ejercicios, cifras y términos técnicos tal cual aparecen.
- No agregues información externa ni resuelvas ejercicios.
- Ignora ruido de la extracción (números de página, encabezados repetidos).
- Responde solo con el resumen, en el idioma del documento, en no más de 800 palabras.
"""
