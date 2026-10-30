"""Seguridad proporcional a uso personal: token local, ids válidos y límites por sesión."""

import re
import secrets
import time
from collections import deque

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse

from config import settings
from config.settings import WS_MAX_TURNS_PER_MINUTE

THREAD_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,100}")

# El navegador no permite cabeceras en el WebSocket: el token viaja como subprotocolo
# "token.<APP_TOKEN>" junto al subprotocolo de la app, que es el que se acepta.
WS_APP_SUBPROTOCOL = "study.v1"
WS_TOKEN_SUBPROTOCOL_PREFIX = "token."


def is_valid_thread_id(thread_id: str) -> bool:
    return THREAD_ID_PATTERN.fullmatch(thread_id) is not None


def validate_chat_id(chat_id: str) -> None:
    if not is_valid_thread_id(chat_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="chat_id_invalido",
        )


def token_matches(candidate: str | None) -> bool:
    # Sin token configurado no se acepta nada: la app no arranca en ese caso (ver lifespan).
    if not settings.APP_TOKEN or candidate is None:
        return False
    return secrets.compare_digest(candidate.encode(), settings.APP_TOKEN.encode())


def websocket_token(subprotocols: list[str]) -> str | None:
    for protocol in subprotocols:
        if protocol.startswith(WS_TOKEN_SUBPROTOCOL_PREFIX):
            return protocol[len(WS_TOKEN_SUBPROTOCOL_PREFIX):]
    return None


class TokenAuthMiddleware:
    """Exige `Authorization: Bearer <APP_TOKEN>` en HTTP; el WS se valida en su endpoint."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Los preflight CORS no llevan cabeceras de autenticación.
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        authorization = dict(scope.get("headers", [])).get(b"authorization", b"").decode("latin-1")
        scheme, _, candidate = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token_matches(candidate.strip()):
            response = JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Token inválido o ausente."},
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class TurnRateLimiter:
    """Ventana deslizante en memoria: acota el gasto si algo envía mensajes en bucle."""

    def __init__(self, max_turns: int = WS_MAX_TURNS_PER_MINUTE, window_seconds: float = 60.0):
        self.max_turns = max_turns
        self.window_seconds = window_seconds
        self._turns: dict[str, deque] = {}

    def allow(self, thread_id: str) -> bool:
        now = time.monotonic()
        turns = self._turns.setdefault(thread_id, deque())
        while turns and now - turns[0] > self.window_seconds:
            turns.popleft()
        if len(turns) >= self.max_turns:
            return False
        turns.append(now)
        return True

    def forget(self, thread_id: str) -> None:
        self._turns.pop(thread_id, None)
