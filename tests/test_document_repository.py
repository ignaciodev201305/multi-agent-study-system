"""Pruebas del catálogo de documentos: cuotas, dedupe, transiciones, recuperación y migración."""

import tempfile
import unittest
from pathlib import Path

import aiosqlite

from documents.repository import DocumentRepository
from documents.pipeline import _pages_from_metadata, split_into_blocks


class DocumentRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "app.db"
        self.repository = await DocumentRepository.open(self.database_path)

    async def asyncTearDown(self):
        await self.repository.close()
        self.directory.cleanup()

    async def reserve(self, document_id, *, sha="a", size=10, max_documents=5, max_bytes=100):
        return await self.repository.reserve_upload(
            document_id=document_id,
            chat_id="chat",
            original_filename=f"{document_id}.pdf",
            document_type="rag",
            size_bytes=size,
            sha256=sha,
            max_documents=max_documents,
            max_bytes=max_bytes,
        )

    async def test_reserve_deduplicates_by_hash(self):
        status, document = await self.reserve("d1")
        self.assertEqual(status, "created")
        self.assertEqual(document["status"], "queued")
        status, duplicate = await self.reserve("d2")
        self.assertEqual(status, "duplicate")
        self.assertEqual(duplicate["document_id"], "d1")

    async def test_quotas(self):
        await self.reserve("d1", sha="a", max_documents=1)
        status, _ = await self.reserve("d2", sha="b", max_documents=1)
        self.assertEqual(status, "document_quota")
        status, _ = await self.reserve("d3", sha="c", size=95)
        self.assertEqual(status, "byte_quota")

    async def test_ready_transition_saves_summary(self):
        await self.reserve("d1")
        claimed = await self.repository.claim_queued_document("d1")
        self.assertEqual(claimed["status"], "processing")
        self.assertEqual(claimed["attempts"], 1)
        self.assertIsNone(await self.repository.claim_queued_document("d1"))

        saved = await self.repository.mark_ready("d1", {"title": "Física", "summary": "Óptica"}, 7)
        self.assertTrue(saved)
        documents = await self.repository.list_ready_with_summaries("chat")
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["summary"]["summary"], "Óptica")
        self.assertEqual(documents[0]["chunks"], 7)

    async def test_result_is_discarded_after_chat_deleted(self):
        await self.reserve("d1")
        await self.repository.claim_queued_document("d1")
        await self.repository.delete_chat("chat")
        self.assertFalse(await self.repository.mark_ready("d1", {"title": "x"}, 1))
        self.assertEqual(await self.repository.list_ready_with_summaries("chat"), [])

    async def test_failed_document_is_not_listed_as_ready(self):
        await self.reserve("d1")
        await self.repository.claim_queued_document("d1")
        await self.repository.mark_failed("d1", "error")
        self.assertEqual(await self.repository.list_ready_with_summaries("chat"), [])
        requeued = await self.repository.requeue_failed_duplicate("d1")
        self.assertEqual(requeued["status"], "queued")

    async def test_recover_jobs_requeues_or_fails_by_attempts(self):
        await self.reserve("d1", sha="a")
        await self.reserve("d2", sha="b")
        await self.repository.claim_queued_document("d1")
        await self.repository.claim_queued_document("d2")
        await self.repository.requeue_document("d2", max_attempts=3)
        await self.repository.claim_queued_document("d2")

        queued = await self.repository.recover_jobs(max_attempts=2)
        self.assertEqual(queued, ["d1"])
        self.assertEqual((await self.repository.get_document("d2"))["status"], "failed")


class RepositoryMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_adds_summary_column_to_old_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "app.db"
            async with aiosqlite.connect(database_path) as connection:
                await connection.execute(
                    """
                    CREATE TABLE documents (
                        document_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL,
                        original_filename TEXT NOT NULL, document_type TEXT NOT NULL,
                        size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
                        status TEXT NOT NULL, chunks INTEGER NOT NULL DEFAULT 0,
                        attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    )
                    """
                )
                await connection.commit()

            repository = await DocumentRepository.open(database_path)
            try:
                await repository.reserve_upload(
                    document_id="d1", chat_id="chat", original_filename="a.pdf",
                    document_type="study_map", size_bytes=1, sha256="a",
                    max_documents=5, max_bytes=100,
                )
                await repository.claim_queued_document("d1")
                self.assertTrue(await repository.mark_ready("d1", {"title": "t"}, 0))
            finally:
                await repository.close()


class SplitIntoBlocksTests(unittest.TestCase):
    def test_groups_and_cuts_text(self):
        blocks = split_into_blocks(["aaaa", "bb", "cccccccccc"], max_chars=7)
        self.assertTrue(all(len(block) <= 7 for block in blocks))
        self.assertEqual("".join(blocks).replace("\n", ""), "aaaabbcccccccccc")

    def test_pages_come_from_docling_provenance(self):
        metadata = {"dl_meta": {"doc_items": [
            {"prov": [{"page_no": 4}, {"page_no": 3}]},
            {"prov": [{"page_no": 4}]},
        ]}}
        self.assertEqual(_pages_from_metadata(metadata), [3, 4])
        self.assertEqual(_pages_from_metadata({}), [])


if __name__ == "__main__":
    unittest.main()
