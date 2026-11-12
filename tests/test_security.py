"""Pruebas del token local en HTTP, de la configuración CORS y del cliente web servido."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from api.app import app as api_app, lifespan
from api.cors import configure_cors
from api.security import TokenAuthMiddleware, TurnRateLimiter, reset_auth_failures
from config.settings import AUTH_MAX_FAILURES_PER_MINUTE

TOKEN = "token-de-prueba"


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


class WebClientTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / "assets").mkdir()
        (root / "index.html").write_text("<p>inicio</p>", encoding="utf-8")
        (root / "assets" / "app.js").write_text("// app", encoding="utf-8")
        (root / "secreto.txt").write_text("no", encoding="utf-8")

        app = _make_app()
        app.mount("/", StaticFiles(directory=root, html=True), name="web")
        for name, value in (("APP_TOKEN", TOKEN), ("SERVE_WEB_CLIENT", True)):
            setting_patch = patch(f"config.settings.{name}", value)
            setting_patch.start()
            self.addCleanup(setting_patch.stop)
        reset_auth_failures()
        self.addCleanup(reset_auth_failures)
        self.client = TestClient(app)

    def test_shell_and_assets_load_without_token(self):
        self.assertIn("inicio", self.client.get("/").text)
        self.assertEqual(self.client.get("/assets/app.js").status_code, 200)

    def test_api_still_needs_token(self):
        self.assertEqual(self.client.get("/chats/a/pdfs").status_code, 401)

    def test_other_files_and_methods_need_token(self):
        self.assertEqual(self.client.get("/secreto.txt").status_code, 401)
        self.assertEqual(self.client.post("/").status_code, 401)

    def test_shell_needs_token_when_not_serving_web_client(self):
        with patch("config.settings.SERVE_WEB_CLIENT", False):
            self.assertEqual(self.client.get("/").status_code, 401)


class WebClientStartupTests(unittest.IsolatedAsyncioTestCase):
    async def _start(self):
        async with lifespan(api_app):
            pass

    async def test_short_token_stops_startup(self):
        with patch("config.settings.SERVE_WEB_CLIENT", True), patch("config.settings.APP_TOKEN", TOKEN):
            with self.assertRaisesRegex(RuntimeError, "APP_TOKEN"):
                await self._start()

    async def test_missing_build_stops_startup(self):
        with (
            patch("config.settings.SERVE_WEB_CLIENT", True),
            patch("config.settings.APP_TOKEN", "t" * 32),
            patch("config.settings.WEB_CLIENT_DIRECTORY", Path("no-existe")),
        ):
            with self.assertRaisesRegex(RuntimeError, "npm run build"):
                await self._start()


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
