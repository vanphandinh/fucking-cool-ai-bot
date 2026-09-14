import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.router import CompletionResult
from app.config import Settings
from app.core.job_manager import JobManager, JobSubmission
from app.core.orchestrator import Orchestrator


class QuestionRenewalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        manager = getattr(self, "manager", None)
        if manager is not None:
            await manager.shutdown()

    async def test_two_renewals_keep_same_job_and_do_not_replay_completed_steps(self):
        settings = Settings(
            _env_file=None,
            question_renewal_interval_sec=1,
            question_progress_interval_sec=.1,
            question_max_inflight_operations=1,
        )
        entered = [asyncio.Event() for _ in range(3)]
        release = [asyncio.Event() for _ in range(3)]
        calls = [0, 0, 0]

        async def execute(record, operations):
            evidence = []
            for index in range(3):
                async def step(i=index):
                    calls[i] += 1
                    entered[i].set()
                    await release[i].wait()
                    return f"evidence-{i}"

                evidence.append(
                    await operations.run(f"step-{index}", step, timeout_sec=1)
                )
            return "|".join(evidence)

        self.manager = JobManager(settings, execute)
        job_id = await self.manager.submit(
            JobSubmission("q", None, 10, -100, 7, 1, 101, [])
        )
        record = self.manager.get(job_id)

        await entered[0].wait()
        record.control.remaining = 0
        self.assertTrue(await record.control.expire_if_due())
        release[0].set()
        await asyncio.sleep(.01)
        self.assertFalse(entered[1].is_set())
        generation1 = record.control.generation
        self.assertEqual(
            await self.manager.renew(job_id, generation1, 10, -100, 101),
            "renewed",
        )

        await entered[1].wait()
        record.control.remaining = 0
        self.assertTrue(await record.control.expire_if_due())
        release[1].set()
        await asyncio.sleep(.01)
        self.assertFalse(entered[2].is_set())
        generation2 = record.control.generation
        self.assertEqual(
            await self.manager.renew(job_id, generation2, 10, -100, 101),
            "renewed",
        )

        await entered[2].wait()
        release[2].set()
        await self.manager.wait(job_id)
        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.renewals, 2)
        self.assertEqual(calls, [1, 1, 1])
        self.assertEqual(self.manager.owned_task_count, 1)  # monitor until shutdown
        await self.manager.shutdown()
        self.assertEqual(self.manager.owned_task_count, 0)
        self.manager = None

    async def test_orchestrator_url_cache_survives_expiry_without_refetch(self):
        settings = Settings(
            _env_file=None,
            question_renewal_interval_sec=1,
            question_progress_interval_sec=.1,
        )
        control_holder = {}

        class FakeRouter:
            async def complete(
                self,
                messages,
                tools,
                tool_executor,
                *,
                requires_vision=False,
                image_count=0,
                operations=None,
            ):
                first = await tool_executor("fetch_url", {"url": "https://example.com"})
                second = await tool_executor("fetch_url", {"url": "https://example.com"})
                control_holder["control"] = operations.control
                self.first = first
                self.second = second
                return CompletionResult("answer", "fake")

        fake_router = FakeRouter()
        orchestrator = Orchestrator(settings, fake_router)
        from app.core.job_control import JobControl
        from app.core.job_operations import OperationRunner

        control = JobControl(renewal_sec=1)
        operations = OperationRunner(control, asyncio.Semaphore(1), lambda event: None)
        read_result = SimpleNamespace(
            ok=True,
            source_url="https://example.com",
            text="evidence",
        )

        async def controlled_read(url, current_settings, mode="auto", *, operations=None):
            self.assertEqual(url, "https://example.com")
            self.assertIs(current_settings, settings)
            self.assertEqual(mode, "auto")
            self.assertIsNotNone(operations)

            async def leaf():
                operations.control.remaining = 0
                self.assertTrue(await operations.control.expire_if_due())
                return read_result

            return await operations.run("mock URL", leaf, timeout_sec=.1)

        read_mock = AsyncMock(side_effect=controlled_read)
        with patch("app.core.orchestrator.url_service.read_url", read_mock):
            answer = await orchestrator.ask("q", operations=operations)

        self.assertEqual(answer.text, "answer")
        self.assertEqual(fake_router.first, fake_router.second)
        self.assertEqual(read_mock.await_count, 1)
        self.assertEqual(control_holder["control"].state, "AWAITING_CONSENT")


if __name__ == "__main__":
    unittest.main()
