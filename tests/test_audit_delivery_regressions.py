"""Failure injection for confirmed Telegram multipart acknowledgments."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app.bot.job_status import render_job_status
from app.bot.question_runner import QuestionProcessor
from app.config import Settings
from app.core.context import ChatMemory
from app.core.job_manager import JobManager, JobSubmission
from app.core.stats import Stats


class _Bot:
    def __init__(self, *, fail_at=None, failure=None, block_at=None, parse_once=False):
        self.calls = []
        self.acknowledged = []
        self.fail_at = fail_at
        self.failure = failure or RuntimeError("transport failed")
        self.block_at = block_at
        self.entered = asyncio.Event()
        self.parse_once = parse_once
        self.parse_failed = False

    async def send_message(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls)
        if index == self.block_at:
            self.entered.set()
            await asyncio.Event().wait()
        if index == self.fail_at:
            raise self.failure
        if self.parse_once and kwargs.get("parse_mode") == "HTML" and not self.parse_failed:
            self.parse_failed = True
            raise TelegramBadRequest(
                method=SendMessage(chat_id=1, text="test"),
                message="Bad Request: can't parse entities",
            )
        self.acknowledged.append(kwargs["text"])
        return SimpleNamespace(message_id=1000 + index)


class AuditDeliveryRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        manager = getattr(self, "manager", None)
        if manager is not None:
            await manager.shutdown()

    async def _run(self, bot, text, *, wait=True, topic_id=7, images=None):
        memory = ChatMemory(max_turns_per_chat=10)
        stats = Stats()
        processor = QuestionProcessor(bot, Settings(_env_file=None), None, memory, stats)
        answer = SimpleNamespace(
            text=text,
            provider="test",
            fallbacks=("fallback",),
            searched=True,
            sources=[],
            images=images or [],
        )

        async def execute(record, operations):
            return answer

        self.manager = JobManager(
            Settings(_env_file=None), execute, processor.deliver,
            delivery_shutdown_grace_sec=0.01,
        )
        job_id = await self.manager.submit(
            JobSubmission("question", None, 10, -100, topic_id, 42, 101, [])
        )
        if wait:
            await asyncio.wait_for(self.manager.wait(job_id), timeout=3)
        return job_id, memory, stats, processor

    async def test_one_part_success_is_committed_once(self):
        bot = _Bot()
        job, memory, stats, processor = await self._run(bot, "<b>answer</b>")
        snapshot = self.manager.snapshot(job)
        self.assertEqual(
            (snapshot.state, snapshot.sent_parts, snapshot.total_parts),
            ("COMPLETED", 1, 1),
        )
        self.assertEqual(memory.history_for((-100, 7))[1]["content"], "answer")
        self.assertEqual(
            (stats.partial_deliveries, stats.searches, stats.fallback_count),
            (0, 1, 1),
        )
        self.assertEqual(stats.by_provider["test"], 1)
        processor._commit_full(self.manager.get(job), SimpleNamespace(text="duplicate"))
        self.assertEqual(stats.by_provider["test"], 1)
        self.assertEqual(len(memory.history_for((-100, 7))), 2)

    async def test_failure_on_first_chunk_records_no_answer(self):
        bot = _Bot(fail_at=1)
        job, memory, stats, _ = await self._run(bot, "A" * 5000)
        self.assertEqual(self.manager.snapshot(job).state, "FAILED")
        self.assertEqual(memory.history_for((-100, 7)), [])
        self.assertEqual(
            (stats.partial_deliveries, stats.searches, stats.by_provider["test"]),
            (0, 0, 0),
        )

    async def test_fail_after_middle_ack_keeps_prefix_only_and_no_replay(self):
        bot = _Bot(fail_at=3)
        job, memory, stats, _ = await self._run(bot, "B" * 10000)
        snapshot = self.manager.snapshot(job)
        self.assertEqual(
            (snapshot.state, snapshot.sent_parts, snapshot.total_parts),
            ("PARTIALLY_DELIVERED", 2, 3),
        )
        history = memory.history_for((-100, 7))
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["content"], "question")
        self.assertTrue(history[1]["content"].endswith("[...] Câu trả lời chưa hoàn chỉnh"))
        self.assertEqual(len(history[1]["content"]), 4000)
        self.assertEqual(history[1]["content"][:20], "B" * 20)
        self.assertEqual(
            (stats.partial_deliveries, stats.searches, stats.by_provider["test"]),
            (1, 0, 0),
        )
        self.assertEqual(len(bot.calls), 3)
        self.assertEqual(self.manager.get(job).acknowledged_parts, [])
        text, keyboard = render_job_status(snapshot, renewal_sec=180, now=snapshot.created_at + 1)
        self.assertIn("2/3", text)
        self.assertIsNone(keyboard)

    async def test_non_parse_bad_request_is_not_retried(self):
        exc = TelegramBadRequest(
            method=SendMessage(chat_id=1, text="test"),
            message="Bad Request: chat not found",
        )
        bot = _Bot(fail_at=1, failure=exc)
        job, _, stats, _ = await self._run(bot, "<b>answer</b>")
        self.assertEqual(self.manager.snapshot(job).state, "FAILED")
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(stats.partial_deliveries, 0)

    async def test_html_parse_failure_plain_fallback_acknowledges_once(self):
        bot = _Bot(parse_once=True)
        job, memory, stats, _ = await self._run(bot, "<b>answer</b>")
        self.assertEqual(self.manager.snapshot(job).state, "COMPLETED")
        self.assertEqual(len(bot.calls), 2)
        self.assertEqual(bot.calls[0]["parse_mode"], "HTML")
        self.assertNotIn("parse_mode", bot.calls[1])
        self.assertEqual(memory.history_for((-100, 7))[1]["content"], "answer")
        self.assertEqual(stats.by_provider["test"], 1)

    async def test_cancel_while_second_chunk_in_flight_keeps_one_ack(self):
        bot = _Bot(block_at=2)
        job, memory, stats, _ = await self._run(bot, "C" * 5000, wait=False)
        await asyncio.wait_for(bot.entered.wait(), timeout=3)
        await self.manager.shutdown()
        snapshot = self.manager.snapshot(job)
        self.assertEqual(
            (snapshot.state, snapshot.sent_parts, snapshot.total_parts),
            ("PARTIALLY_DELIVERED", 1, 2),
        )
        self.assertEqual(stats.partial_deliveries, 1)
        self.assertEqual(stats.by_provider["test"], 0)
        self.assertTrue(memory.history_for((-100, 7))[1]["content"].endswith("chưa hoàn chỉnh"))

    async def test_shutdown_during_interchunk_sleep_commits_only_first_ack(self):
        bot = _Bot()
        sleeping = asyncio.Event()
        original_sleep = asyncio.sleep

        async def controlled_sleep(seconds):
            if seconds == 0.15:
                sleeping.set()
                await asyncio.Event().wait()
            else:
                await original_sleep(seconds)

        with patch("app.bot.question_runner.asyncio.sleep", new=controlled_sleep):
            job, memory, stats, _ = await self._run(bot, "S" * 5000, wait=False)
            await asyncio.wait_for(sleeping.wait(), timeout=2)
            await self.manager.shutdown()

        snapshot = self.manager.snapshot(job)
        self.assertEqual((snapshot.state, snapshot.sent_parts, snapshot.total_parts),
                         ("PARTIALLY_DELIVERED", 1, 2))
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(stats.partial_deliveries, 1)
        self.assertEqual(len(memory.history_for((-100, 7))), 2)

    async def test_partial_state_rejects_stale_callbacks_and_releases_admission(self):
        bot = _Bot(fail_at=2)
        job, _, _, _ = await self._run(bot, "A" * 5000)
        self.assertEqual(self.manager.snapshot(job).state, "PARTIALLY_DELIVERED")
        self.assertEqual(await self.manager.renew(job, 0, 10, -100, 101), "stale")
        self.assertEqual(await self.manager.stop(job, 10, -100, 101), "terminal")

        successor = await self.manager.submit(
            JobSubmission("later", None, 10, -100, 7, 43, 102, [])
        )
        await self.manager.wait(successor)
        self.assertEqual(self.manager.snapshot(successor).state, "COMPLETED")

    async def test_timeout_at_second_chunk_keeps_acknowledged_prefix(self):
        bot = _Bot(fail_at=2, failure=TimeoutError("private provider token"))
        job, memory, stats, _ = await self._run(bot, "D" * 5000)
        self.assertEqual(self.manager.snapshot(job).state, "PARTIALLY_DELIVERED")
        self.assertEqual(stats.partial_deliveries, 1)
        self.assertEqual(len(memory.history_for((-100, 7))), 2)

    async def test_extras_failure_after_full_ack_does_not_change_outcome(self):
        async def exploding_images(*args, **kwargs):
            raise RuntimeError("images unavailable")

        bot = _Bot()
        with patch("app.bot.question_runner.send_image_results", new=exploding_images):
            job, memory, stats, _ = await self._run(bot, "answer", images=["image"])
        self.assertEqual(self.manager.snapshot(job).state, "COMPLETED")
        self.assertEqual(stats.by_provider["test"], 1)
        self.assertEqual(len(memory.history_for((-100, 7))), 2)


if __name__ == "__main__":
    unittest.main()
