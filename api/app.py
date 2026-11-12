"""API FastAPI: lifespan (grafos, workers de documentos) y wiring de rutas y middleware."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from api.cors import configure_cors
from api.routes import router as chats_router
from api.security import MAX_UPLOAD_REQUEST_BYTES, PdfUploadBodyLimitMiddleware, TokenAuthMiddleware
from api.ws import SessionRegistry, router as ws_router
from config import settings
from config.settings import (
    APP_DATABASE_PATH,
    CHROMA_DIRECTORY,
    DATA_DIRECTORY,
    DOCUMENT_MAX_ATTEMPTS,
    DOCUMENT_WORKERS,
    PDF_DIRECTORY,
)
from core.chats import ChatRepository
from core.graphs import StudyGraph
from core.model_catalog import ModelCatalog
from core.model_settings import ModelResolver, ModelSettingsRepository
from core.study.repository import ProgressRepository
from core.usage import UsageRepository
from documents.cleanup import cleanup_orphan_documents
from documents.pipeline import DoclingExecutor, DocumentIndexer, DocumentPipeline
from documents.repository import DocumentRepository
from documents.worker import document_worker

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Compila los grafos e inicia/recupera el procesamiento asíncrono de PDFs."""
    if not settings.APP_TOKEN:
        raise RuntimeError(
            "Falta APP_TOKEN en el entorno (.env). Genera uno con "
            "'python -c \"import secrets; print(secrets.token_hex(32))\"' y configúralo también en el cliente."
        )
    app.state.session_registry = SessionRegistry()
    app.state.document_repository = await DocumentRepository.open(APP_DATABASE_PATH)
    app.state.progress_repository = await ProgressRepository.open(APP_DATABASE_PATH)
    app.state.chat_repository = await ChatRepository.open(APP_DATABASE_PATH)
    app.state.usage_repository = await UsageRepository.open(APP_DATABASE_PATH)
    app.state.model_catalog = ModelCatalog()
    app.state.model_settings = await ModelSettingsRepository.open(APP_DATABASE_PATH)
    app.state.model_resolver = ModelResolver(app.state.model_settings, app.state.model_catalog)
    app.state.document_queue = asyncio.Queue()
    app.state.document_workers = []
    app.state.docling_executor = None

    try:
        app.state.study_graph_instance = StudyGraph()
        app.state.study_graph = await app.state.study_graph_instance.compile_graph()
        app.state.docling_executor = DoclingExecutor()
        document_pipeline = DocumentPipeline(
            app.state.docling_executor,
            DocumentIndexer,
            app.state.model_resolver,
        )

        await cleanup_orphan_documents(
            app.state.document_repository,
            Path(DATA_DIRECTORY),
            PDF_DIRECTORY,
            CHROMA_DIRECTORY,
        )
        queued_document_ids = await app.state.document_repository.recover_jobs(
            DOCUMENT_MAX_ATTEMPTS
        )
        for document_id in queued_document_ids:
            app.state.document_queue.put_nowait(document_id)

        app.state.document_workers = [
            asyncio.create_task(
                document_worker(
                    app.state.document_queue,
                    app.state.document_repository,
                    document_pipeline,
                    app.state.usage_repository,
                ),
                name=f"document-worker-{index}",
            )
            for index in range(DOCUMENT_WORKERS)
        ]
        yield
    finally:
        for worker in app.state.document_workers:
            worker.cancel()
        if app.state.document_workers:
            await asyncio.gather(*app.state.document_workers, return_exceptions=True)

        if app.state.docling_executor is not None:
            app.state.docling_executor.shutdown()

        await app.state.document_repository.requeue_processing(DOCUMENT_MAX_ATTEMPTS)
        study_graph = getattr(app.state, "study_graph", None)
        if study_graph is not None:
            await study_graph.checkpointer.conn.close()
        await app.state.model_settings.close()
        await app.state.usage_repository.close()
        await app.state.chat_repository.close()
        await app.state.progress_repository.close()
        await app.state.document_repository.close()

app = FastAPI(lifespan=lifespan)

# CORS envuelve a ambos para que también agregue sus encabezados al 401 y al 413.
app.add_middleware(
    PdfUploadBodyLimitMiddleware,
    max_body_bytes=MAX_UPLOAD_REQUEST_BYTES,
)
app.add_middleware(TokenAuthMiddleware)
configure_cors(app)

app.include_router(ws_router)
app.include_router(chats_router)
