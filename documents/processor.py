"""Carga e indexación de PDFs en Chroma para RAG y mapas de estudio."""

import hashlib
import logging
import os

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_docling.loader import DoclingLoader
from langchain_openai import OpenAIEmbeddings

from config.settings import OPENAI_API_BASE, COLLECTION_NAME, PDF_DIRECTORY, CHROMA_DIRECTORY, DATA_DIRECTORY

load_dotenv()

logger = logging.getLogger(__name__)


class DocumentProcessor:
    """Extrae el contenido de un PDF usando Docling."""
    def load_document(self, file_path):
        """Carga el PDF y devuelve sus documentos con el texto extraído."""
        options = PdfPipelineOptions()
        options.do_table_structure = False
        
        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options = options
                )
            }
        )


        loader = DoclingLoader(file_path=file_path, converter=converter)
        return loader.load()

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
                embedding_function=OpenAIEmbeddings(model="qwen/qwen3-embedding-8b", openai_api_base=OPENAI_API_BASE,check_embedding_ctx_length=False),
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
                "chunk_index": i
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
        path_document = os.path.join(self.path_pdf, document_id)
        
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
