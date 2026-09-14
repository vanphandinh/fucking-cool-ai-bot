import asyncio
import unittest

from app.config import Settings
from app.core.job_manager import JobManager, JobSubmission


class TerminalPayloadPruningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        manager = getattr(self, "manager", None)
        if manager is not None:
            await manager.shutdown()

    def _assert_pruned(self, job_id, *, state, owner_id=10, chat_id=-100):
        record = self.manager.get(job_id)
        self.assertIsNotNone(record)
        self.assertEqual(record.submission.question, "")
        self.assertIsNone(record.submission.quoted)
        self.assertEqual(record.submission.history, [])
        self.assertIsNone(record.submission.prepare_request)
        self.assertIsNone(record.prepared_request)
        self.assertIsNone(record.operations)
        self.assertIsNone(record.result)
        self.assertEqual(record.events, [])

        snapshot = self.manager.snapshot(job_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.owner_id, owner_id)
        self.assertEqual(snapshot.chat_id, chat_id)
        self.assertEqual(snapshot.request_message_id, 1)
        self.assertEqual(snapshot.status_message_id, 101)
        self.assertEqual(snapshot.state, state)

    async def test_completed_job_releases_large_payload_but_keeps_snapshot_identity(self):
        settings = Settings(_env_file=None)

        async def prepare(operations):
            return {"prepared": "large payload"}

        async def execute(record, operations):
            return {"answer": "large result"}

        self.manager = JobManager(settings, execute)
        job_id = await self.manager.submit(
            JobSubmission(
                "large question",
                "large quoted text",
                10,
                -100,
                7,
                1,
                101,
                [{"role": "user", "content": "large history"}],
                prepare_request=prepare,
            )
        )
        await self.manager.wait(job_id)

        self._assert_pruned(job_id, state="COMPLETED")
        self.assertTrue(self.manager.get(job_id).delivered)

    async def test_failed_prepare_releases_prepare_closure_and_payload(self):
        settings = Settings(_env_file=None)
        captured = object()

        async def prepare(operations):
            _ = captured
            raise RuntimeError("prepare failed")

        async def execute(record, operations):
            self.fail("execute should not run after prepare failure")

        self.manager = JobManager(settings, execute)
        job_id = await self.manager.submit(
            JobSubmission(
                "large question",
                "quoted",
                10,
                -100,
                None,
                1,
                101,
                [{"role": "user", "content": "history"}],
                prepare_request=prepare,
            )
        )
        await self.manager.wait(job_id)

        self._assert_pruned(job_id, state="FAILED")
        self.assertIsNotNone(self.manager.snapshot(job_id).error)

    async def test_cancelled_job_releases_large_payload(self):
        settings = Settings(_env_file=None)
        entered = asyncio.Event()

        async def execute(record, operations):
            entered.set()
            await asyncio.Event().wait()

        self.manager = JobManager(settings, execute)
        job_id = await self.manager.submit(
            JobSubmission(
                "large question",
                "quoted",
                10,
                -100,
                None,
                1,
                101,
                [{"role": "user", "content": "history"}],
            )
        )
        await entered.wait()
        result = await self.manager.stop(job_id, 10, -100, 101)
        self.assertEqual(result, "stopped")
        await self.manager.wait(job_id)

        self._assert_pruned(job_id, state="CANCELLED")


if __name__ == "__main__":
    unittest.main()
