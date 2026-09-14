import asyncio
import unittest

from app.config import Settings
from app.core.job_manager import JobCapacityError, JobManager, JobSubmission


class JobManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        manager = getattr(self, "manager", None)
        if manager is not None:
            await manager.shutdown()

    async def test_paused_job_does_not_block_second_job_and_history_is_snapshot(self):
        settings = Settings(
            question_renewal_interval_sec=60,
            question_max_pending_jobs=4,
            question_max_jobs_per_user=2,
            question_max_inflight_operations=1,
        )
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_done = asyncio.Event()
        seen_history = {}

        async def execute(record, operations):
            seen_history[record.submission.request_message_id] = list(record.submission.history)
            if record.submission.request_message_id == 1:
                first_started.set()
                await release_first.wait()
                return "A"
            second_done.set()
            return "B"

        self.manager = JobManager(settings, execute)
        shared_history = [{"role": "user", "content": "old"}]
        first = await self.manager.submit(
            JobSubmission("a", None, 10, -100, None, 1, 101, shared_history)
        )
        await first_started.wait()
        shared_history.append({"role": "assistant", "content": "later"})
        second = await self.manager.submit(
            JobSubmission("b", None, 11, -100, None, 2, 102, shared_history)
        )
        await asyncio.wait_for(second_done.wait(), .2)
        await self.manager.wait(second)
        self.assertEqual(seen_history[1], [{"role": "user", "content": "old"}])
        self.assertEqual(len(seen_history[2]), 2)
        release_first.set()
        await self.manager.wait(first)

    async def test_caps_and_duplicate_submission_are_atomic(self):
        settings = Settings(
            question_max_pending_jobs=2,
            question_max_jobs_per_user=1,
        )
        release = asyncio.Event()

        async def execute(record, operations):
            await release.wait()
            return "ok"

        self.manager = JobManager(settings, execute)
        submission = JobSubmission("a", None, 10, -100, 7, 1, 101, [])
        first = await self.manager.submit(submission)
        duplicate = await self.manager.submit(submission)
        self.assertEqual(first, duplicate)
        with self.assertRaises(JobCapacityError):
            await self.manager.submit(JobSubmission("b", None, 10, -100, 7, 2, 102, []))
        release.set()
        await self.manager.wait(first)

    async def test_owner_renews_once_and_stop_cancels_owned_task(self):
        settings = Settings(question_renewal_interval_sec=1)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def execute(record, operations):
            async def blocked():
                entered.set()
                await release.wait()
                return "x"

            await operations.run("work", blocked, timeout_sec=10)
            return "answer"

        self.manager = JobManager(settings, execute)
        job_id = await self.manager.submit(
            JobSubmission("a", None, 10, -100, None, 1, 101, [])
        )
        await entered.wait()
        record = self.manager.get(job_id)
        record.control.remaining = 0
        self.assertTrue(await record.control.expire_if_due())
        generation = record.control.generation
        self.assertEqual(
            await self.manager.renew(job_id, generation, 10, -100, 101),
            "renewed",
        )
        self.assertEqual(
            await self.manager.renew(job_id, generation, 10, -100, 101),
            "stale",
        )
        self.assertEqual(await self.manager.stop(job_id, 10, -100, 101), "stopped")
        await self.manager.wait(job_id)
        self.assertEqual(self.manager.snapshot(job_id).state, "CANCELLED")

    async def test_admin_can_stop_but_cannot_renew_and_forged_target_is_rejected(self):
        settings = Settings(admin_ids="99", question_renewal_interval_sec=1)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def execute(record, operations):
            async def blocked():
                entered.set()
                await release.wait()
                return "x"

            await operations.run("work", blocked, timeout_sec=10)
            return "answer"

        self.manager = JobManager(settings, execute)
        job_id = await self.manager.submit(
            JobSubmission("a", None, 10, -100, None, 1, 101, [])
        )
        await entered.wait()
        record = self.manager.get(job_id)
        record.control.remaining = 0
        self.assertTrue(await record.control.expire_if_due())
        generation = record.control.generation

        self.assertEqual(
            await self.manager.renew(job_id, generation, 99, -100, 101),
            "forbidden",
        )
        self.assertEqual(
            await self.manager.stop(job_id, 10, -999, 101),
            "forbidden",
        )
        self.assertEqual(
            await self.manager.stop(job_id, 10, -100, 999),
            "forbidden",
        )
        self.assertEqual(await self.manager.stop(job_id, 99, -100, 101), "stopped")

    async def test_shutdown_leaves_no_owned_tasks(self):
        settings = Settings()
        started = asyncio.Event()

        async def execute(record, operations):
            started.set()
            await asyncio.Event().wait()

        self.manager = JobManager(settings, execute)
        await self.manager.submit(JobSubmission("a", None, 10, -100, None, 1, 101, []))
        await started.wait()
        await self.manager.shutdown()
        self.assertEqual(self.manager.owned_task_count, 0)

    async def test_shutdown_gives_delivery_a_bounded_grace_then_marks_interrupted(self):
        settings = Settings()
        delivering = asyncio.Event()
        release_delivery = asyncio.Event()

        async def execute(record, operations):
            return "answer"

        async def deliver(record, result):
            delivering.set()
            await release_delivery.wait()

        self.manager = JobManager(
            settings,
            execute,
            deliver,
            delivery_shutdown_grace_sec=.01,
        )
        job_id = await self.manager.submit(
            JobSubmission("a", None, 10, -100, None, 1, 101, [])
        )
        await delivering.wait()
        await self.manager.shutdown()
        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "FAILED")
        self.assertIn("gián đoạn", snapshot.error)
        self.assertEqual(self.manager.owned_task_count, 0)
