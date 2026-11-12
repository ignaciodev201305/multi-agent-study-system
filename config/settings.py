"""Configuración de modelos, límites y rutas usadas por los grafos del sistema."""

import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()



def _model_role(
    role: str,
    model: str,
    temperature: float,
    reasoning_effort: str | None = None,
    max_tokens: int | None = None,
) -> dict:
    """Modelo de un rol con override por entorno: MODEL_, TEMPERATURE_, REASONING_ y MAX_TOKENS_<ROL>."""
    key = role.upper()
    max_tokens = os.environ.get(f"MAX_TOKENS_{key}", max_tokens)
    return {
        "model": os.environ.get(f"MODEL_{key}", model),
        "temperature": float(os.environ.get(f"TEMPERATURE_{key}", temperature)),
        "reasoning_effort": os.environ.get(f"REASONING_{key}", reasoning_effort) or None,
        "max_tokens": int(max_tokens) if max_tokens else None,
    }


# Modelo, temperatura, esfuerzo de razonamiento y tope de salida de cada rol.
# El tope es una red de seguridad para roles de salida acotada; en algunos proveedores incluye el
# razonamiento, por eso es amplio. Supervisor y especialistas no lo llevan: sus explicaciones varían.
MODEL_ROLES = {
    "context_classifier": _model_role("context_classifier", "google/gemini-3.1-flash-lite", 0.0, "medium", 4000),
    "factory": _model_role("factory", "google/gemini-3.1-flash-lite", 0.0, "medium", 6000),
    "supervisor": _model_role("supervisor", "x-ai/grok-4.7", 0.0),
    "subagent_light": _model_role("subagent_light", "deepseek/deepseek-v4.1-flash", 0.2),
    "subagent_heavy": _model_role("subagent_heavy", "deepseek/deepseek-v4.1-flash", 0.2),
    "summarizer": _model_role("summarizer", "anthropic/claude-haiku-5.5", 0.0, max_tokens=6000),
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
# Mensajes de la transcripción que se devuelven por sesión.
CHAT_MESSAGES_MAX_ENTRIES = max(1, int(os.environ.get("CHAT_MESSAGES_MAX_ENTRIES", "500")))
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
# Largo mínimo del token cuando la API se publica fuera de esta máquina.
REMOTE_TOKEN_MIN_CHARS = 32
# Intentos fallidos de autenticación por minuto antes de responder 429.
AUTH_MAX_FAILURES_PER_MINUTE = max(1, int(os.environ.get("AUTH_MAX_FAILURES_PER_MINUTE", "10")))


def _extra_origins(value: str) -> tuple[str, ...]:
    """Lee orígenes https exactos separados por comas; rechaza comodines y rutas."""
    origins = tuple(origin.strip() for origin in value.split(",") if origin.strip())
    for origin in origins:
        if re.fullmatch(r"https://[A-Za-z0-9.\-]+(:\d{1,5})?", origin) is None:
            raise RuntimeError(
                f"ALLOWED_EXTRA_ORIGINS admite solo orígenes https exactos, sin ruta ni comodines: {origin}"
            )
    return origins


# Orígenes adicionales del cliente web, p. ej. el proxy HTTPS de la red ZeroTier.
ALLOWED_EXTRA_ORIGINS = _extra_origins(os.environ.get("ALLOWED_EXTRA_ORIGINS", ""))
# Límites del WebSocket para acotar el costo.
WS_MAX_MESSAGE_CHARS = max(1, int(os.environ.get("WS_MAX_MESSAGE_CHARS", "8000")))
WS_MAX_TURNS_PER_MINUTE = max(1, int(os.environ.get("WS_MAX_TURNS_PER_MINUTE", "20")))

# Tiempo máximo de cada cálculo de la tool math.
MATH_TIMEOUT_SECONDS = max(1, int(os.environ.get("MATH_TIMEOUT_SECONDS", "8")))
# Tiempo máximo de cada cálculo de las tools de área.
TOOL_WORKER_TIMEOUT_SECONDS = max(1, int(os.environ.get("TOOL_WORKER_TIMEOUT_SECONDS", "10")))
# Pasos máximos del supervisor por turno.
SUPERVISOR_RECURSION_LIMIT = max(5, int(os.environ.get("SUPERVISOR_RECURSION_LIMIT", "40")))
# Llamadas al modelo por turno; la última se hace sin tools para que el agente cierre con una respuesta.
SUPERVISOR_MAX_MODEL_CALLS = max(2, int(os.environ.get("SUPERVISOR_MAX_MODEL_CALLS", "8")))
SPECIALIST_MAX_MODEL_CALLS = max(2, int(os.environ.get("SPECIALIST_MAX_MODEL_CALLS", "6")))
# Límites del supervisor en modo profundo: planifica y encadena más pasos en un mismo turno.
DEEP_SUPERVISOR_RECURSION_LIMIT = max(5, int(os.environ.get("DEEP_SUPERVISOR_RECURSION_LIMIT", "150")))
DEEP_SUPERVISOR_MAX_MODEL_CALLS = max(2, int(os.environ.get("DEEP_SUPERVISOR_MAX_MODEL_CALLS", "20")))

# Límites de la pizarra de cada sesión.
BOARD_MAX_BLOCKS = max(1, int(os.environ.get("BOARD_MAX_BLOCKS", "60")))
BOARD_MAX_BLOCK_CHARS = max(1, int(os.environ.get("BOARD_MAX_BLOCK_CHARS", "8000")))
BOARD_MAX_OPS_PER_TURN = max(1, int(os.environ.get("BOARD_MAX_OPS_PER_TURN", "8")))
BOARD_MAX_PLOT_FUNCTIONS = max(1, int(os.environ.get("BOARD_MAX_PLOT_FUNCTIONS", "4")))
