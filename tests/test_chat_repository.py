"""Pruebas de la transcripción en SQLite real y de los endpoints de sesiones."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
from core.chats import ChatRepository

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


class ChatRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repository = await ChatRepository.open(Path(self.directory.name) / "app.db")

    async def asyncTearDown(self):
        await self.repository.close()
        self.directory.cleanup()

    async def test_first_message_creates_chat_with_trimmed_title(self):
        await self.repository.add_message("a", "user", "   " + "x" * 60, NOW)
        chat = (await self.repository.list_chats(10))[0]
        self.assertEqual(chat["title"], "x" * 42)
        self.assertEqual(chat["phase"], "setup")
        self.assertEqual(chat["created_at"], "2026-01-10T12:00:00+00:00")

    async def test_title_comes_only_from_first_message(self):
        await self.repository.add_message("a", "user", "Derivadas", NOW)
        await self.repository.add_message("a", "user", "Otra cosa", NOW)
        self.assertEqual((await self.repository.list_chats(10))[0]["title"], "Derivadas")

    async def test_phase_and_title_are_updated(self):
        await self.repository.add_message("a", "user", "hola", NOW)
        later = NOW + timedelta(minutes=1)
        await self.repository.add_message(
            "a", "system", "Listo", later, phase="study", title="Cálculo"
        )
        chat = (await self.repository.list_chats(10))[0]
        self.assertEqual((chat["phase"], chat["title"]), ("study", "Cálculo"))
        self.assertEqual(chat["updated_at"], "2026-01-10T12:01:00+00:00")
        self.assertEqual(chat["created_at"], "2026-01-10T12:00:00+00:00")

    async def test_message_without_phase_keeps_previous_one(self):
        await self.repository.add_message("a", "user", "hola", NOW, phase="clarification")
        await self.repository.add_message("a", "user", "otra", NOW)
        self.assertEqual((await self.repository.list_chats(10))[0]["phase"], "clarification")

    async def test_messages_keep_order_and_fields(self):
        await self.repository.add_message("a", "user", "uno", NOW)
        await self.repository.add_message("a", "assistant", "dos", NOW, agent="Supervisor")
        messages = await self.repository.list_messages("a", 10)
        self.assertEqual([message["content"] for message in messages], ["uno", "dos"])
        self.assertEqual([message["agent"] for message in messages], [None, "Supervisor"])
        self.assertLess(messages[0]["id"], messages[1]["id"])

    async def test_identical_messages_are_both_kept(self):
        await self.repository.add_message("a", "assistant", "igual", NOW)
        await self.repository.add_message("a", "assistant", "igual", NOW)
        self.assertEqual(len(await self.repository.list_messages("a", 10)), 2)

    async def test_list_messages_returns_the_last_ones(self):
        for index in range(5):
            await self.repository.add_message("a", "user", f"m{index}", NOW)
        messages = await self.repository.list_messages("a", 3)
        self.assertEqual([message["content"] for message in messages], ["m2", "m3", "m4"])

    async def test_chats_are_ordered_by_last_update(self):
        await self.repository.add_message("a", "user", "viejo", NOW)
        await self.repository.add_message("b", "user", "nuevo", NOW + timedelta(hours=1))
        await self.repository.add_message("a", "user", "otra", NOW + timedelta(hours=2))
        self.assertEqual([chat["chat_id"] for chat in await self.repository.list_chats(10)], ["a", "b"])
        self.assertEqual(len(await self.repository.list_chats(1)), 1)

    async def test_titles_returns_only_existing_chats(self):
        await self.repository.add_message("a", "user", "Uno", NOW)
        await self.repository.add_message("b", "user", "Dos", NOW)
        self.assertEqual(await self.repository.titles(["a", "b", "c"]), {"a": "Uno", "b": "Dos"})
        self.assertEqual(await self.repository.titles([]), {})

    async def test_delete_removes_chat_and_messages_only_for_that_chat(self):
        await self.repository.add_message("a", "user", "uno", NOW)
        await self.repository.add_message("b", "user", "dos", NOW)
        await self.repository.delete_chat("a")
        self.assertEqual(await self.repository.list_messages("a", 10), [])
        self.assertEqual([chat["chat_id"] for chat in await self.repository.list_chats(10)], ["b"])
        self.assertEqual(len(await self.repository.list_messages("b", 10)), 1)

    async def test_messages_do_not_leak_between_chats(self):
        await self.repository.add_message("a", "user", "uno", NOW)
        await self.repository.add_message("b", "user", "dos", NOW)
        contents = [message["content"] for message in await self.repository.list_messages("b", 10)]
        self.assertEqual(contents, ["dos"])


class ChatEndpointTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        self.chats = SimpleNamespace(
            list_chats=AsyncMock(return_value=[
                {"chat_id": "a", "title": "Uno", "phase": "study", "created_at": "t1", "updated_at": "t2"},
                {"chat_id": "b", "title": "Dos", "phase": "setup", "created_at": "t1", "updated_at": "t1"},
            ]),
            list_messages=AsyncMock(return_value=[
                {"id": 7, "role": "user", "agent": None, "content": "hola", "created_at": "t1"},
            ]),
            delete_chat=AsyncMock(),
        )
        app.state.chat_repository = self.chats
        app.state.document_repository = SimpleNamespace(
            count_by_chat=AsyncMock(return_value={"a": 3}), delete_chat=AsyncMock()
        )
        app.state.progress_repository = SimpleNamespace(
            list_for_chat=AsyncMock(return_value=[]), delete_chat=AsyncMock()
        )
        app.state.model_settings = SimpleNamespace(delete_chat=AsyncMock())
        app.state.usage_repository = SimpleNamespace(delete_chat=AsyncMock())
        app.state.model_resolver = SimpleNamespace()
        app.state.study_graph = SimpleNamespace(
            aget_state=AsyncMock(return_value=SimpleNamespace(values={}, interrupts=())),
            checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
        )
        app.state.study_graph_instance = SimpleNamespace(
            session=SimpleNamespace(remove_session_cache_entry=lambda chat_id: None)
        )
        app.state.session_registry = SimpleNamespace(
            is_turn_active=lambda chat_id: False,
            rate_limiter=SimpleNamespace(forget=lambda chat_id: None),
        )
        self.client = TestClient(app)

    def test_list_chats_includes_document_count(self):
        chats = self.client.get("/chats").json()["chats"]
        self.assertEqual([(chat["chat_id"], chat["document_count"]) for chat in chats], [("a", 3), ("b", 0)])
        self.assertEqual(chats[0]["title"], "Uno")

    def test_messages_come_from_the_transcript(self):
        with patch("api.routes.session_usage", AsyncMock(return_value={})):
            body = self.client.get("/chats/thread/messages").json()
        self.assertEqual(body["messages"], [
            {"id": "7", "role": "user", "agent": None, "content": "hola", "created_at": "t1"},
        ])
        self.assertEqual(body["phase"], "setup")
        self.chats.list_messages.assert_awaited_once_with("thread", routes.CHAT_MESSAGES_MAX_ENTRIES)

    def test_delete_removes_transcript(self):
        with patch("api.routes.SessionManager"), patch("api.routes.forget_vector_store"):
            response = self.client.delete("/chats/thread")
        self.assertEqual(response.status_code, 200)
        self.chats.delete_chat.assert_awaited_once_with("thread")


if __name__ == "__main__":
    unittest.main()
