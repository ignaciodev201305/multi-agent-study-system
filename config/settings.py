"""Configuración de modelos, límites y rutas usadas por los grafos del sistema."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()



def _model_role(role: str, model: str, temperature: float, reasoning_effort: str | None = None) -> dict:
    """Modelo de un rol con override por entorno: MODEL_<ROL>, TEMPERATURE_<ROL>, REASONING_<ROL>."""
    key = role.upper()
    return {
        "model": os.environ.get(f"MODEL_{key}", model),
        "temperature": float(os.environ.get(f"TEMPERATURE_{key}", temperature)),
        "reasoning_effort": os.environ.get(f"REASONING_{key}", reasoning_effort) or None,
    }


# Modelo, temperatura y esfuerzo de razonamiento de cada rol.
MODEL_ROLES = {
    "context_classifier": _model_role("context_classifier", "google/gemini-3.1-flash-lite", 0.0, "medium"),
    "factory": _model_role("factory", "google/gemini-3.1-flash-lite", 0.0, "medium"),
    "supervisor": _model_role("supervisor", "x-ai/grok-4.7", 0.0),
    "subagent_light": _model_role("subagent_light", "deepseek/deepseek-v4.1-flash", 0.2),
    "subagent_heavy": _model_role("subagent_heavy", "deepseek/deepseek-v4.1-flash", 0.2),
    "summarizer": _model_role("summarizer", "anthropic/claude-haiku-5.5", 0.0),
}
EMBEDDINGS_MODEL = os.environ.get("MODEL_EMBEDDINGS", "qwen/qwen3-embedding-8b")

OPENAI_API_BASE="https://openrouter.ai/api/v1"

# Mensajes máximos del historial y cuántos se conservan al resumir.
SESSION_HISTORY_MAX_MESSAGES = 10
SESSION_HISTORY_KEEP_MESSAGES = 4

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIRECTORY = BASE_DIR/"data/sessions/"
os.makedirs(DATA_DIRECTORY, exist_ok=True)
APP_DATABASE_PATH = BASE_DIR / "data" / "app.db"
STUDY_DATABASE_PATH = BASE_DIR / "data" / "study.db"
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

# Límites de procesamiento de cada PDF en Docling.
DOCLING_MAX_PAGES = max(1, int(os.environ.get("DOCLING_MAX_PAGES", "400")))
DOCLING_TIMEOUT_SECONDS = max(10, int(os.environ.get("DOCLING_TIMEOUT_SECONDS", "900")))
# Figuras por PDF que se describen con el modelo de visión; las demás se omiten.
DOCUMENT_MAX_FIGURES = max(0, int(os.environ.get("DOCUMENT_MAX_FIGURES", "30")))
# Lado mínimo en píxeles para describir una figura (descarta íconos y viñetas).
DOCUMENT_FIGURE_MIN_SIDE = 100
# Lado máximo en píxeles con que se envía cada figura; acota los tokens por imagen.
DOCUMENT_FIGURE_MAX_SIDE = 768
# Caracteres máximos por prompt de resumen antes de usar map-reduce.
DOCUMENT_SUMMARY_CHAR_BUDGET = max(
    10_000, int(os.environ.get("DOCUMENT_SUMMARY_CHAR_BUDGET", "120000"))
)

# Token local que comparten el backend y el cliente.
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()
# Límites del WebSocket para acotar el costo.
WS_MAX_MESSAGE_CHARS = max(1, int(os.environ.get("WS_MAX_MESSAGE_CHARS", "8000")))
WS_MAX_TURNS_PER_MINUTE = max(1, int(os.environ.get("WS_MAX_TURNS_PER_MINUTE", "20")))

# Tiempo máximo de cada cálculo de la tool math.
MATH_TIMEOUT_SECONDS = max(1, int(os.environ.get("MATH_TIMEOUT_SECONDS", "8")))
# Pasos máximos del supervisor por turno.
SUPERVISOR_RECURSION_LIMIT = max(5, int(os.environ.get("SUPERVISOR_RECURSION_LIMIT", "40")))

# Límites de la pizarra de cada sesión.
BOARD_MAX_BLOCKS = max(1, int(os.environ.get("BOARD_MAX_BLOCKS", "60")))
BOARD_MAX_BLOCK_CHARS = max(1, int(os.environ.get("BOARD_MAX_BLOCK_CHARS", "8000")))
BOARD_MAX_OPS_PER_TURN = max(1, int(os.environ.get("BOARD_MAX_OPS_PER_TURN", "8")))
BOARD_MAX_PLOT_FUNCTIONS = max(1, int(os.environ.get("BOARD_MAX_PLOT_FUNCTIONS", "4")))
