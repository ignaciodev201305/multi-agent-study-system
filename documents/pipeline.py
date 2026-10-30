"""Pipeline lineal de procesamiento de PDFs: Docling en un proceso aparte, índice Chroma y resumen."""

import asyncio
import hashlib
import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

from config.settings import (
    CHROMA_DIRECTORY,
    COLLECTION_NAME,
    DATA_DIRECTORY,
    DOCLING_MAX_PAGES,
    DOCLING_TIMEOUT_SECONDS,
    DOCUMENT_SUMMARY_CHAR_BUDGET,
    PDF_DIRECTORY,
)
from core.chain import ChainBuilder
from core.llm import build_embeddings, build_model
from core.prompts.documents import BUILD_RAG_CONTEXT_PROMPT, MAP_STUDY_PROMPT, PARTIAL_SUMMARY_PROMPT
from core.schemas import MapStudy, RagContext

logger = logging.getLogger(__name__)

# El converter carga modelos pesados: se crea una sola vez por proceso hijo.
_converter = None


def _get_converter():
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        options = PdfPipelineOptions()
        options.do_table_structure = False
        _converter = DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
        )
    return _converter


def _pages_from_metadata(metadata: dict) -> list[int]:
    """Extrae los números de página que Docling deja en dl_meta.doc_items[].prov[]."""
    pages = set()
    for item in (metadata.get("dl_meta") or {}).get("doc_items", []):
        for prov in item.get("prov", []):
            if isinstance(prov.get("page_no"), int):
                pages.add(prov["page_no"])
    return sorted(pages)


def extract_chunks(pdf_path: str, max_pages: int) -> list[dict]:
    """Corre en el proceso hijo: convierte el PDF y devuelve fragmentos serializables."""
    from langchain_docling.loader import DoclingLoader

    loader = DoclingLoader(
        file_path=pdf_path,
        converter=_get_converter(),
        convert_kwargs={"max_num_pages": max_pages},
    )
    return [
        {"page_content": document.page_content, "pages": _pages_from_metadata(document.metadata)}
        for document in loader.load()
    ]


class DoclingExecutor:
    """Pool de un proceso para Docling: no compite por el GIL con el chat y se puede matar."""

    def __init__(self):
        self._context = multiprocessing.get_context("spawn")
        self._pool = self._new_pool()

    def _new_pool(self) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=1, mp_context=self._context)

    async def extract(self, pdf_path: Path) -> list[dict]:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._pool, extract_chunks, str(pdf_path), DOCLING_MAX_PAGES)
        try:
            return await asyncio.wait_for(future, timeout=DOCLING_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            # Un PDF bomba no debe dejar el proceso ocupado: se mata y se crea otro pool.
            self._kill_pool()
            raise

    def _kill_pool(self) -> None:
        # ProcessPoolExecutor no permite cancelar una tarea en curso; se terminan sus procesos.
        self.shutdown()
        self._pool = self._new_pool()

    def shutdown(self) -> None:
        for process in list(getattr(self._pool, "_processes", {}).values()):
            process.terminate()
        self._pool.shutdown(wait=False, cancel_futures=True)


class DocumentIndexer:
    """Gestiona el vector store de una sesión y la indexación de sus PDFs."""
    def _init_paths(self, session_id):
        self.session_id = session_id
        self.chroma_db = os.path.join(DATA_DIRECTORY/session_id,CHROMA_DIRECTORY)
        self.path_pdf = os.path.join(DATA_DIRECTORY/session_id,PDF_DIRECTORY)
        self._create_vector_store()

    def _create_vector_store(self):
        try:   
            self.vectorstore = Chroma(
                collection_name=COLLECTION_NAME,
                embedding_function=build_embeddings(),
                persist_directory=self.chroma_db
            )
        except Exception:
            logger.exception("Fallo al inicializar Chroma o la colección vectorial")
            self.vectorstore = None
            self.collection = None
    
    def _prepare_documents(self, documents, source):
        prepared = []
        ids = []

        for i, document in enumerate(documents):
            if not isinstance(document.page_content, str):
                raise TypeError("Document.page_content debe ser str")
            texto = document.page_content.strip()
            if not texto:
                continue

            metadata = {
                "session_id": self.session_id,
                "document_id": source,
                "source": f"{self.path_pdf}/{source}.pdf",
                "chunk_index": i,
                # Chroma solo admite metadata escalar: páginas como "3,4".
                "pages": ",".join(str(page) for page in document.metadata.get("pages", [])),
            }
            # El id se deriva del contenido para evitar duplicados del mismo fragmento.
            content_id = f"{self.session_id}\0{self.path_pdf+"/"+source}\0{i}\0{texto}"
            document_id = hashlib.sha256(content_id.encode("utf-8")).hexdigest()

            prepared.append(
                Document(id=document_id, page_content=texto, metadata=metadata)
                )
            ids.append(document_id)
        self.documents_prepared = prepared
        self.ids = ids
    
    def index_pdf(self, documents, document_id, session_id):
        """Reemplaza los fragmentos previos del documento e indexa los nuevos."""
        self._init_paths(session_id)
        
        self._prepare_documents(documents , document_id)
        if not self.documents_prepared:
            return False
        
        if not self.vectorstore:
            return False

        self.vectorstore.delete(where={"document_id": document_id})
        self.vectorstore.add_documents(documents=self.documents_prepared, ids=self.ids)
        return True
    
    def get_vector_store(self, session_id):
        """Devuelve el vector store de la sesión o None si falla la inicialización."""
        self._init_paths(session_id)
        return self.vectorstore

    def delete_orphan_vectors(self, session_id, valid_document_ids):
        """Borra chunks huérfanos sin requerir inicializar embeddings."""
        chroma_db = os.path.join(DATA_DIRECTORY / session_id, CHROMA_DIRECTORY)
        if not os.path.isdir(chroma_db):
            return 0

        vectorstore = Chroma(
            collection_name=COLLECTION_NAME,
            persist_directory=chroma_db,
        )
        stored = vectorstore.get(include=["metadatas"])
        vector_ids = stored.get("ids", [])
        metadatas = stored.get("metadatas") or []
        orphan_ids = []

        for index, vector_id in enumerate(vector_ids):
            metadata = metadatas[index] if index < len(metadatas) else None
            document_id = metadata.get("document_id") if isinstance(metadata, dict) else None
            if document_id not in valid_document_ids:
                orphan_ids.append(vector_id)

        if orphan_ids:
            vectorstore.delete(ids=orphan_ids)
        return len(orphan_ids)


class DocumentPipeline:
    """Procesa un documento y devuelve el resultado que el worker guarda en app.db."""

    def __init__(self, executor: DoclingExecutor, indexer_factory):
        self.executor = executor
        self.indexer_factory = indexer_factory
        self.chain_builder = ChainBuilder()
        self.model = build_model("summarizer")

    async def process(self, document: dict, pdf_path: Path) -> tuple[dict, int]:
        """Devuelve (resumen como dict, número de fragmentos indexados)."""
        chunks = await self.executor.extract(pdf_path)
        if document["document_type"] == "rag":
            return await self.process_rag(document, chunks)
        return await self.process_study_map(chunks)

    async def process_rag(self, document: dict, chunks: list[dict]) -> tuple[dict, int]:
        documents = [
            Document(page_content=chunk["page_content"], metadata={"pages": chunk["pages"]})
            for chunk in chunks
        ]
        indexer = self.indexer_factory()
        indexed = await asyncio.to_thread(
            indexer.index_pdf, documents, document["document_id"], document["chat_id"]
        )
        if not indexed:
            raise RuntimeError("El documento no tiene texto indexable.")
        summary = await self._summarize(chunks, BUILD_RAG_CONTEXT_PROMPT, RagContext)
        return summary.model_dump(), len(indexer.documents_prepared)

    async def process_study_map(self, chunks: list[dict]) -> tuple[dict, int]:
        summary = await self._summarize(chunks, MAP_STUDY_PROMPT, MapStudy)
        return summary.model_dump(), 0

    async def _summarize(self, chunks: list[dict], prompt: str, schema):
        """Resumen estructurado; si el texto supera el presupuesto, map-reduce por bloques."""
        text = "\n".join(chunk["page_content"] for chunk in chunks)
        if len(text) > DOCUMENT_SUMMARY_CHAR_BUDGET:
            text = await self._reduce(chunks)
        chain = self.chain_builder.build_chain(self.model, prompt, schema)
        return await chain.ainvoke({"documents": text})

    async def _reduce(self, chunks: list[dict]) -> str:
        blocks = split_into_blocks(
            [chunk["page_content"] for chunk in chunks], DOCUMENT_SUMMARY_CHAR_BUDGET // 2
        )
        chain = self.chain_builder.build_chain(self.model, PARTIAL_SUMMARY_PROMPT)
        partials = []
        # Secuencial a propósito: acota el gasto y el rate limit del proveedor.
        for index, block in enumerate(blocks, start=1):
            response = await chain.ainvoke({"block": block, "index": index, "total": len(blocks)})
            partials.append(f"[Parte {index}/{len(blocks)}]\n{response.content}")
        reduced = "\n\n".join(partials)
        if len(reduced) > DOCUMENT_SUMMARY_CHAR_BUDGET:
            return reduced[:DOCUMENT_SUMMARY_CHAR_BUDGET]
        return reduced


def split_into_blocks(texts: list[str], max_chars: int) -> list[str]:
    """Agrupa fragmentos consecutivos en bloques de hasta max_chars (corta los gigantes)."""
    blocks: list[str] = []
    current = ""
    for text in texts:
        while len(text) > max_chars:
            if current:
                blocks.append(current)
                current = ""
            blocks.append(text[:max_chars])
            text = text[max_chars:]
        if current and len(current) + len(text) + 1 > max_chars:
            blocks.append(current)
            current = ""
        current = f"{current}\n{text}" if current else text
    if current:
        blocks.append(current)
    return blocks
