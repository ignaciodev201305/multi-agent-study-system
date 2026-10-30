"""Herramientas de búsqueda web y de consulta al vector store por sesión."""

from langchain.tools import tool
from langchain_tavily import TavilySearch

from documents.processor import DocumentIndexer

class ToolsBuilder:
    """
    Las funciones se encargan de retornar la lista de tools para un agente
    """
    def get_tavily_tools(self):
        """Devuelve las herramientas de búsqueda web para el clasificador de contexto."""
        return [TavilySearch()]
    
    def get_vector_store_tools(self, session_id):
        """Devuelve la tool de búsqueda semántica limitada a los PDFs de la sesión."""
        @tool
        def search_vector_store(query: str) -> str:
            """Busca fragmentos relevantes en los PDFs subidos como tipo rag en esta sesión.

                Úsala cuando el usuario pregunte por contenido específico de esos PDFs:
                definiciones, datos, ejemplos, ejercicios o citas.
                No la uses para el mapa de estudio ni para preguntas generales de la conversación.
                La consulta debe ser específica y en lenguaje natural; puedes llamarla más de una vez.
                Devuelve hasta 4 fragmentos con su documento y número de fragmento.
                Si no hay resultados, dilo; no inventes contenido.
            """
            indexer=DocumentIndexer().get_vector_store(session_id)
            
            if not indexer:
                return "No hay documentos rag indexados en esta sesión." 

            try:
                docs = indexer.similarity_search(query, k=4, filter={"session_id": session_id})
            except Exception as e:
                return "No se pudieron consultar los PDFs en este momento."
            
            parts = []
            for doc in docs:
                source = doc.metadata.get("source", "document")
                name_document = source.rsplit("/", 1)[-1]
                chunk = doc.metadata.get("chunk_index") 
                chunk = "?" if chunk is None else chunk
                parts.append(
                    f"{name_document} - fragmento {chunk} \n{doc.page_content}"
                )            
            return "\n".join(parts)
        return [search_vector_store]
