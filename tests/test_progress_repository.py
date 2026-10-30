"""Pruebas del progreso en SQLite real y de sus endpoints HTTP."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import routes
from core.schemas import StudyPlan
from core.study.repository import ProgressRepository

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)
PLAN = StudyPlan.model_validate({
    "subject": "derivadas",
    "syllabus": ["Definición", "Regla de la cadena"],
    "supervisor_focus": "Principiante.",
    "archetypes": [{"archetype": "explicador", "focus": "Derivadas."}],
})


def _update(number=1, status="dominado", evidence="Respondió bien."):
    return {"topic_number": number, "topic": f"Tema {number}", "status": status, "evidence": evidence}


class ProgressRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "app.db"
        self.repository = await ProgressRepository.open(self.database)

    async def asyncTearDown(self):
        await self.repository.close()
        self.directory.cleanup()

    async def test_first_update_starts_from_first_box(self):
        await self.repository.apply_updates("c", [_update(status="en_progreso")], NOW)
        topic = (await self.repository.list_for_chat("c"))[0]
        self.assertEqual(topic["topic"], "Tema 1")
        self.assertEqual(topic["box"], 0)
        self.assertEqual(topic["due_at"], "2026-01-11T12:00:00+00:00")
        self.assertEqual(topic["updated_at"], "2026-01-10T12:00:00+00:00")

    async def test_box_advances_between_turns(self):
        await self.repository.apply_updates("c", [_update()], NOW)
        await self.repository.apply_updates("c", [_update()], NOW + timedelta(days=3))
        topic = (await self.repository.list_for_chat("c"))[0]
        self.assertEqual(topic["box"], 2)
        self.assertEqual(topic["due_at"], "2026-01-20T12:00:00+00:00")

    async def test_difficulty_resets_box_and_keeps_history(self):
        await self.repository.apply_updates("c", [_update(evidence="primera")], NOW)
        await self.repository.apply_updates(
            "c", [_update(status="con_dificultad", evidence="segunda")], NOW + timedelta(days=3)
        )
        topic = (await self.repository.list_for_chat("c"))[0]
        self.assertEqual((topic["box"], topic["status"]), (0, "con_dificultad"))
        self.assertEqual([review["evidence"] for review in topic["reviews"]], ["segunda", "primera"])

    async def test_reviews_are_limited_per_topic(self):
        for index in range(5):
            await self.repository.apply_updates("c", [_update(evidence=f"e{index}")], NOW)
        reviews = (await self.repository.list_for_chat("c"))[0]["reviews"]
        self.assertEqual([review["evidence"] for review in reviews], ["e4", "e3", "e2"])

    async def test_failed_update_rolls_back_whole_turn(self):
        broken = {"topic_number": 2, "topic": "Tema 2", "status": "dominado", "evidence": None}
        with self.assertRaises(Exception):
            await self.repository.apply_updates("c", [_update(), broken], NOW)
        self.assertEqual(await self.repository.list_for_chat("c"), [])

        await self.repository.apply_updates("c", [_update()], NOW)
        self.assertEqual(len(await self.repository.list_for_chat("c")), 1)

    async def test_empty_updates_do_nothing(self):
        await self.repository.apply_updates("c", [], NOW)
        self.assertEqual(await self.repository.list_for_chat("c"), [])

    async def test_list_due_crosses_chats_and_orders_by_date(self):
        await self.repository.apply_updates("a", [_update(status="en_progreso")], NOW)
        await self.repository.apply_updates("b", [_update(status="en_progreso")], NOW - timedelta(days=1))
        await self.repository.apply_updates("c", [_update()], NOW)

        due = await self.repository.list_due(NOW + timedelta(days=1), 10)
        self.assertEqual([entry["chat_id"] for entry in due], ["b", "a"])
        self.assertEqual(set(due[0]), {"chat_id", "topic_number", "topic", "status", "due_at"})
        self.assertEqual(len(await self.repository.list_due(NOW + timedelta(days=1), 1)), 1)
        self.assertEqual(await self.repository.list_due(NOW - timedelta(seconds=1), 10), [])

    async def test_due_comparison_uses_utc(self):
        await self.repository.apply_updates("c", [_update(status="en_progreso")], NOW)
        local = timezone(timedelta(hours=-3))
        self.assertEqual(await self.repository.list_due((NOW + timedelta(hours=23)).astimezone(local), 10), [])
        self.assertEqual(len(await self.repository.list_due((NOW + timedelta(hours=24)).astimezone(local), 10)), 1)

    async def test_delete_chat_removes_only_that_chat(self):
        await self.repository.apply_updates("a", [_update()], NOW)
        await self.repository.apply_updates("b", [_update()], NOW)
        await self.repository.delete_chat("a")

        self.assertEqual(await self.repository.list_for_chat("a"), [])
        self.assertEqual(len(await self.repository.list_for_chat("b")), 1)
        await self.repository.apply_updates("a", [_update()], NOW)
        self.assertEqual(len((await self.repository.list_for_chat("a"))[0]["reviews"]), 1)

    async def test_data_survives_reopening(self):
        await self.repository.apply_updates("c", [_update()], NOW)
        reopened = await ProgressRepository.open(self.database)
        try:
            self.assertEqual(await reopened.list_for_chat("c"), await self.repository.list_for_chat("c"))
        finally:
            await reopened.close()


class ProgressEndpointTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        self.repository = SimpleNamespace(
            list_for_chat=AsyncMock(return_value=[{"topic_number": 1}]),
            list_due=AsyncMock(return_value=[{"chat_id": "c"}]),
            delete_chat=AsyncMock(),
        )
        app.state.progress_repository = self.repository
        app.state.study_graph = SimpleNamespace(
            aget_state=AsyncMock(return_value=SimpleNamespace(values={"study_plan": PLAN}, interrupts=())),
            checkpointer=SimpleNamespace(adelete_thread=AsyncMock()),
        )
        app.state.document_repository = SimpleNamespace(delete_chat=AsyncMock())
        app.state.study_graph_instance = MagicMock()
        app.state.session_registry = MagicMock()
        app.state.session_registry.is_turn_active.return_value = False
        self.client = TestClient(app)

    def test_progress_includes_syllabus(self):
        response = self.client.get("/chats/thread/progress")
        self.assertEqual(response.json(), {
            "chat_id": "thread",
            "syllabus": ["Definición", "Regla de la cadena"],
            "topics": [{"topic_number": 1}],
        })

    def test_progress_before_plan_is_empty(self):
        self.client.app.state.study_graph.aget_state.return_value = SimpleNamespace(values={}, interrupts=())
        self.repository.list_for_chat.return_value = []
        self.assertEqual(self.client.get("/chats/thread/progress").json()["syllabus"], [])

    def test_snapshot_includes_progress(self):
        response = self.client.get("/chats/thread/messages")
        self.assertEqual(response.json()["progress"], [{"topic_number": 1}])

    def test_snapshot_includes_turn_active(self):
        self.assertIs(self.client.get("/chats/thread/messages").json()["turn_active"], False)
        self.client.app.state.session_registry.is_turn_active.return_value = True
        self.assertIs(self.client.get("/chats/thread/messages").json()["turn_active"], True)

    def test_snapshot_reads_turn_before_checkpoint(self):
        calls = MagicMock()
        calls.attach_mock(self.client.app.state.session_registry.is_turn_active, "turn")
        calls.attach_mock(self.client.app.state.study_graph.aget_state, "state")
        self.client.get("/chats/thread/messages")
        self.assertEqual([call[0] for call in calls.mock_calls], ["turn", "state"])

    def test_due_reviews_use_current_time_and_limit(self):
        response = self.client.get("/reviews/due", params={"limit": 7})
        self.assertEqual(response.json(), {"reviews": [{"chat_id": "c"}]})
        now, limit = self.repository.list_due.await_args.args
        self.assertEqual(limit, 7)
        self.assertLess(abs(datetime.now(timezone.utc) - now), timedelta(minutes=1))

    def test_due_reviews_default_and_invalid_limit(self):
        self.client.get("/reviews/due")
        self.assertEqual(self.repository.list_due.await_args.args[1], 20)
        self.assertEqual(self.client.get("/reviews/due", params={"limit": 0}).status_code, 422)

    def test_delete_chat_removes_progress(self):
        with patch("api.routes.SessionManager"), patch("api.routes.forget_vector_store"):
            response = self.client.delete("/chats/thread")
        self.assertEqual(response.status_code, 200)
        self.repository.delete_chat.assert_awaited_once_with("thread")


if __name__ == "__main__":
    unittest.main()
