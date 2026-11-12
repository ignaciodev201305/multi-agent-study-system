"""Pruebas del token local en HTTP, de la configuración CORS y del acceso remoto."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.app import app as api_app, lifespan
from api.cors import ALLOWED_ORIGINS, configure_cors
from api.security import TokenAuthMiddleware, TurnRateLimiter, reset_auth_failures
from api.ws import origin_allowed
from config.settings import AUTH_MAX_FAILURES_PER_MINUTE, _extra_origins

TOKEN = "token-de-prueba"
REMOTE_ORIGIN = "https://10.147.17.5:8443"


def _make_app():
    app = FastAPI()

    @app.get("/chats/{chat_id}/pdfs")
    async def endpoint(chat_id: str):
        return {"chat_id": chat_id}

    app.add_middleware(TokenAuthMiddleware)
    configure_cors(app)
    return app


class TokenAuthTests(unittest.TestCase):
    def setUp(self):
        token_patch = patch("config.settings.APP_TOKEN", TOKEN)
        token_patch.start()
        self.addCleanup(token_patch.stop)
        reset_auth_failures()
        self.addCleanup(reset_auth_failures)
        self.client = TestClient(_make_app())

    def test_request_without_token_is_401(self):
        self.assertEqual(self.client.get("/chats/a/pdfs").status_code, 401)

    def test_request_with_wrong_token_is_401(self):
        response = self.client.get("/chats/a/pdfs", headers={"Authorization": "Bearer otro"})
        self.assertEqual(response.status_code, 401)

    def test_request_with_token_passes(self):
        response = self.client.get("/chats/a/pdfs", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(response.json(), {"chat_id": "a"})

    def test_preflight_does_not_need_token(self):
        response = self.client.options(
            "/chats/a/pdfs",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_401_includes_cors_headers_for_allowed_origin(self):
        response = self.client.get("/chats/a/pdfs", headers={"Origin": "http://127.0.0.1:5173"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:5173")

    def test_null_origin_is_not_allowed_by_cors(self):
        response = self.client.get(
            "/chats/a/pdfs",
            headers={"Origin": "null", "Authorization": f"Bearer {TOKEN}"},
        )
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_empty_configured_token_rejects_everything(self):
        with patch("config.settings.APP_TOKEN", ""):
            response = self.client.get("/chats/a/pdfs", headers={"Authorization": "Bearer "})
        self.assertEqual(response.status_code, 401)


    def test_repeated_failures_get_429_and_valid_token_still_passes(self):
        for _ in range(AUTH_MAX_FAILURES_PER_MINUTE):
            self.assertEqual(self.client.get("/chats/a/pdfs").status_code, 401)
        self.assertEqual(self.client.get("/chats/a/pdfs").status_code, 429)
        response = self.client.get("/chats/a/pdfs", headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(response.status_code, 200)

    def test_rejection_does_not_echo_received_token(self):
        with self.assertLogs(level="DEBUG") as logs:
            response = self.client.get("/chats/a/pdfs", headers={"Authorization": "Bearer secreto-enviado"})
        self.assertNotIn("secreto-enviado", response.text)
        self.assertNotIn("secreto-enviado", " ".join(logs.output))


class ExtraOriginTests(unittest.TestCase):
    def test_parses_exact_https_origins(self):
        self.assertEqual(
            _extra_origins(f" {REMOTE_ORIGIN} , https://tutor.example "),
            (REMOTE_ORIGIN, "https://tutor.example"),
        )
        self.assertEqual(_extra_origins(""), ())

    def test_rejects_wildcards_paths_and_plain_http(self):
        for origin in ("*", "https://*.example", "http://10.147.17.5:8443", f"{REMOTE_ORIGIN}/api"):
            with self.subTest(origin=origin), self.assertRaises(RuntimeError):
                _extra_origins(origin)

    def test_extra_origin_is_allowed_and_lookalike_is_not(self):
        origins = (*ALLOWED_ORIGINS, REMOTE_ORIGIN)
        with patch("api.ws.ALLOWED_ORIGINS", origins), patch("api.cors.ALLOWED_ORIGINS", origins):
            self.assertTrue(origin_allowed(REMOTE_ORIGIN))
            self.assertFalse(origin_allowed("https://10.147.17.5:9443"))
            self.assertFalse(origin_allowed(f"{REMOTE_ORIGIN}.evil.example"))
            self.assertFalse(origin_allowed(None))
            client = TestClient(_make_app())
            with patch("config.settings.APP_TOKEN", TOKEN):
                response = client.get(
                    "/chats/a/pdfs",
                    headers={"Origin": REMOTE_ORIGIN, "Authorization": f"Bearer {TOKEN}"},
                )
        self.assertEqual(response.headers["access-control-allow-origin"], REMOTE_ORIGIN)


class RemoteTokenTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_rejects_short_token_with_extra_origins(self):
        with (
            patch("config.settings.APP_TOKEN", "corto"),
            patch("config.settings.ALLOWED_EXTRA_ORIGINS", (REMOTE_ORIGIN,)),
            self.assertRaisesRegex(RuntimeError, "al menos"),
        ):
            async with lifespan(api_app):
                pass


class TurnRateLimiterTests(unittest.TestCase):
    def test_window_limits_turns_per_session(self):
        limiter = TurnRateLimiter(max_turns=2, window_seconds=60)
        self.assertTrue(limiter.allow("a"))
        self.assertTrue(limiter.allow("a"))
        self.assertFalse(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))
        limiter.forget("a")
        self.assertTrue(limiter.allow("a"))


if __name__ == "__main__":
    unittest.main()
