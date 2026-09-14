import asyncio
import unittest
from types import SimpleNamespace

from app.bot.question_runner import QuestionProcessor
from app.config import Settings
from app.core.job_manager import JobManager, JobSubmission


class _Memory:
    def __init__(self):
        self.exchanges = []

    def push_exchange(self, key, question, answer, *, order_key=None):
        self.exchanges.append((key, question, answer, order_key))


class _Stats:
    def __init__(self):
        self.fallback_calls = 0
        self.answer_calls = 0
        self.search_calls = 0

    def record_fallbacks(self, fallbacks):
        self.fallback_calls += 1

    def record_answer(self, provider):
        self.answer_calls += 1

    def record_search(self):
        self.search_calls += 1


class _DeliveryBot:
    def __init__(self, *, block_primary=False):
        self.block_primary = block_primary
        self.primary_entered = asyncio.Event()
        self.footer_entered = asyncio.Event()
        self.primary_sends = 0
        self.footer_sends = 0

    async def send_message(self, **kwargs):
        if "reply_parameters" in kwargs:
            self.primary_entered.set()
            if self.block_primary:
                await asyncio.Event().wait()
            self.primary_sends += 1
            return SimpleNamespace(message_id=1001)
        self.footer_entered.set()
        self.footer_sends += 1
        await asyncio.Event().wait()


class _FailSecondPrimaryBot:
    def __init__(self):
        self.calls = 0

    async def send_message(self, **kwargs):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("second primary part failed")
        return SimpleNamespace(message_id=1000 + self.calls)


class QuestionDeliveryShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        manager = getattr(self, "manager", None)
        if manager is not None:
            await manager.shutdown()

    @staticmethod
    def _answer(text="answer"):
        return SimpleNamespace(
            text=text,
            provider="test-provider",
            fallbacks=[],
            searched=True,
            sources=[{"title": "Example", "url": "https://example.com/source"}],
            images=[],
        )

    async def _submit(self, *, bot, answer=None):
        settings = Settings(_env_file=None)
        memory = _Memory()
        stats = _Stats()
        processor = QuestionProcessor(
            bot,
            settings,
            orchestrator=SimpleNamespace(),
            memory=memory,
            stats=stats,
        )
        answer = answer or self._answer()

        async def execute(record, operations):
            return answer

        self.manager = JobManager(
            settings,
            execute,
            processor.deliver,
            delivery_shutdown_grace_sec=.01,
        )
        job_id = await self.manager.submit(
            JobSubmission("question", None, 10, -100, None, 1, 101, [])
        )
        return job_id, memory, stats

    async def test_shutdown_before_primary_delivery_commit_is_failed(self):
        bot = _DeliveryBot(block_primary=True)
        job_id, memory, stats = await self._submit(bot=bot)
        await asyncio.wait_for(bot.primary_entered.wait(), timeout=1)

        await self.manager.shutdown()

        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "FAILED")
        self.assertEqual(bot.primary_sends, 0)
        self.assertEqual(len(memory.exchanges), 0)
        self.assertEqual(stats.answer_calls, 0)
        self.assertEqual(stats.search_calls, 0)

    async def test_shutdown_after_primary_commit_remains_completed(self):
        bot = _DeliveryBot()
        job_id, memory, stats = await self._submit(bot=bot)
        await asyncio.wait_for(bot.footer_entered.wait(), timeout=1)

        await self.manager.shutdown()

        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(bot.primary_sends, 1)
        self.assertEqual(len(memory.exchanges), 1)
        self.assertEqual(memory.exchanges[0][3], 1)
        self.assertEqual(stats.fallback_calls, 1)
        self.assertEqual(stats.answer_calls, 1)
        self.assertEqual(stats.search_calls, 1)
        self.assertEqual(self.manager.owned_task_count, 0)

    async def test_partial_multipart_failure_does_not_commit_memory_or_success_stats(self):
        bot = _FailSecondPrimaryBot()
        answer = self._answer("A" * 5000)
        job_id, memory, stats = await self._submit(bot=bot, answer=answer)

        await asyncio.wait_for(self.manager.wait(job_id), timeout=1)

        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "FAILED")
        self.assertEqual(bot.calls, 2)
        self.assertEqual(memory.exchanges, [])
        self.assertEqual(stats.fallback_calls, 0)
        self.assertEqual(stats.answer_calls, 0)
        self.assertEqual(stats.search_calls, 0)


if __name__ == "__main__":
    unittest.main()
