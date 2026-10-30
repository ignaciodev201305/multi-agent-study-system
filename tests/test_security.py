"""Pruebas del token local en HTTP y de la configuración CORS."""

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.cors import configure_cors
from api.security import TokenAuthMiddleware, TurnRateLimiter

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
