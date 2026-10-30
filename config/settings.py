"""Configuración de modelos, límites y rutas usadas por los grafos del sistema."""

import os
from pathlib import Path

CONTEXT_STUDY_MODEL = "google/gemini-3.1-flash-lite"
CONTEXT_STUDY_TEMPERATURE = 0.0

SUPERVISOR_MODEL = "x-ai/grok-4.7"
SUPERVISOR_TEMPERATURE = 0.0

SUBAGENT_MODEL = "deepseek/deepseek-v4.1-flash"
SUBAGENT_TEMPERATURE = 0.2

CONTEXT_MODEL = "anthropic/claude-haiku-5.5"

OPENAI_API_BASE="https://openrouter.ai/api/v1"

# Historial de sesión: al superar el máximo se resumen los antiguos y se conservan los últimos.
SESSION_HISTORY_MAX_MESSAGES = 10
SESSION_HISTORY_KEEP_MESSAGES = 4

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIRECTORY = BASE_DIR/"data/sessions/"
os.makedirs(DATA_DIRECTORY, exist_ok=True)
APP_DATABASE_PATH = BASE_DIR / "data" / "app.db"
DOCUMENT_WORKERS = max(1, int(os.environ.get("DOCUMENT_WORKERS", "1")))
DOCUMENT_MAX_ATTEMPTS = max(1, int(os.environ.get("DOCUMENT_MAX_ATTEMPTS", "3")))
SESSION_STUDY_SUPERVISOR_CACHE_MAX_ENTRIES = max(
    1, int(os.environ.get("SESSION_STUDY_SUPERVISOR_CACHE_MAX_ENTRIES", "100"))
)
PDF_DIRECTORY = "pdfs"
CHROMA_DIRECTORY = "chroma"

COLLECTION_NAME = "material_estudio"

MAX_PDF_SIZE_BYTES = 20 * 1024 * 1024 
UPLOAD_CHUNK_SIZE = 1024 * 1024        
MAX_DOCUMENTS_PER_CHAT = 20
MAX_PDF_BYTES_PER_CHAT = 200 * 1024 * 1024

# Docling corre en un proceso aparte; un PDF que supere estos límites se marca como fallido.
DOCLING_MAX_PAGES = max(1, int(os.environ.get("DOCLING_MAX_PAGES", "400")))
DOCLING_TIMEOUT_SECONDS = max(10, int(os.environ.get("DOCLING_TIMEOUT_SECONDS", "900")))
# Texto máximo que se envía en un único prompt de resumen; por encima se hace map-reduce.
DOCUMENT_SUMMARY_CHAR_BUDGET = max(
    10_000, int(os.environ.get("DOCUMENT_SUMMARY_CHAR_BUDGET", "120000"))
)
