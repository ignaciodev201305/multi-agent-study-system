"""Configuración CORS de la API, aislada del resto del backend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import ALLOWED_EXTRA_ORIGINS

# Orígenes locales usados por el front (Vite, Docker y Electron empaquetado).
# Electron empaquetado se sirve como app://bundle; "null" no se acepta por seguridad.
# Los orígenes extra salen de ALLOWED_EXTRA_ORIGINS (proxy HTTPS para otros dispositivos).
ALLOWED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://[::1]:5173",
    "app://bundle",
    *ALLOWED_EXTRA_ORIGINS,
)

# Permite cualquier puerto local por si Vite cambia de puerto.
ALLOWED_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"


def configure_cors(app: FastAPI) -> None:
    """Registra el middleware CORS con los orígenes permitidos del front."""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_origin_regex=ALLOWED_ORIGIN_REGEX,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
