from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.bot.handlers import _ChatLocks, _handle_question
from app.config import Settings
from app.core.context import ChatMemory
from app.core.orchestrator import Answer
from app.core.rate_limiter import RateLimiter
from app.core.stats import Stats


def _message(*, thread_id: int, user_id: int = 42):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=-100200),
        message_thread_id=thread_id,
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
        reply=AsyncMock(),
    )


class ForumTopicIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_different_topics_do_not_share_conversation_history(self):
        histories: list[list[dict]] = []

        async def answer(**kwargs):
            histories.append(list(kwargs.get("history") or []))
            return Answer(text="ok", provider="fake")

        settings = Settings(_env_file=None, question_timeout_sec=2.0)
        memory = ChatMemory(max_turns_per_chat=3)
        locks = _ChatLocks()
        media_loader = SimpleNamespace(load=AsyncMock(return_value=()))

        with patch("app.bot.handlers._send_answer_parts", new=AsyncMock(return_value=True)):
            await _handle_question(
                _message(thread_id=101),
                "topic one",
                None,
                settings,
                SimpleNamespace(ask=answer),
                memory,
                RateLimiter(max_requests_per_min=100),
                Stats(),
                locks,
                media_loader,
            )
            await _handle_question(
                _message(thread_id=202),
                "topic two",
                None,
                settings,
                SimpleNamespace(ask=answer),
                memory,
                RateLimiter(max_requests_per_min=100),
                Stats(),
                locks,
                media_loader,
            )

        self.assertEqual(histories[0], [])
        self.assertEqual(
            histories[1],
            [],
            "forum topics in one supergroup must not inherit each other's history",
        )

    async def test_slow_topic_does_not_block_another_topic_in_same_chat(self):
        first_started = asyncio.Event()
        second_started = asyncio.Event()
        release_first = asyncio.Event()

        async def answer(**kwargs):
            question = kwargs.get("question")
            if question == "first":
                first_started.set()
                await release_first.wait()
            else:
                second_started.set()
            return Answer(text="ok", provider="fake")

        settings = Settings(_env_file=None, question_timeout_sec=2.0)
        memory = ChatMemory(max_turns_per_chat=3)
        locks = _ChatLocks()
        media_loader = SimpleNamespace(load=AsyncMock(return_value=()))
        limiter = RateLimiter(max_requests_per_min=100)
        stats = Stats()

        with patch("app.bot.handlers._send_answer_parts", new=AsyncMock(return_value=True)):
            first = asyncio.create_task(
                _handle_question(
                    _message(thread_id=101, user_id=1),
                    "first",
                    None,
                    settings,
                    SimpleNamespace(ask=answer),
                    memory,
                    limiter,
                    stats,
                    locks,
                    media_loader,
                )
            )
            await asyncio.wait_for(first_started.wait(), timeout=0.5)
            second = asyncio.create_task(
                _handle_question(
                    _message(thread_id=202, user_id=2),
                    "second",
                    None,
                    settings,
                    SimpleNamespace(ask=answer),
                    memory,
                    limiter,
                    stats,
                    locks,
                    media_loader,
                )
            )

            try:
                await asyncio.wait_for(second_started.wait(), timeout=0.2)
                progressed = True
            except TimeoutError:
                progressed = False
            finally:
                release_first.set()
                await asyncio.gather(first, second)

        self.assertTrue(
            progressed,
            "one forum topic must not consume the per-conversation lock of another topic",
        )


class TopicLockLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_topic_serializes_many_waiters_and_reclaims(self):
        locks = _ChatLocks()
        active = 0
        max_active = 0

        async def work():
            nonlocal active, max_active
            async with locks.hold(-100200, 101):
                active += 1
                max_active = max(active, max_active)
                await asyncio.sleep(0)
                active -= 1

        await asyncio.gather(*(work() for _ in range(80)))
        self.assertEqual(max_active, 1)
        self.assertEqual(locks._locks, {})

    async def test_different_topics_are_parallel(self):
        locks = _ChatLocks()
        started = asyncio.Event()
        release = asyncio.Event()

        async def first():
            async with locks.hold(10, 1):
                started.set()
                await release.wait()

        task = asyncio.create_task(first())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            async with asyncio.timeout(1):
                async with locks.hold(10, 2):
                    self.assertEqual(len(locks._locks), 2)
        finally:
            release.set()
            await task
        self.assertEqual(locks._locks, {})

    async def test_cancellation_while_waiting_retains_owner(self):
        locks = _ChatLocks()
        async with locks.hold(10, 1):
            waiter = asyncio.create_task(self._acquire(locks, 10, 1))
            await asyncio.sleep(0)
            entry = locks._locks[(10, 1)]
            self.assertEqual(entry.references, 2)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertIs(locks._locks[(10, 1)], entry)
            self.assertEqual(entry.references, 1)
        self.assertEqual(locks._locks, {})

    async def test_timeout_while_waiting_reclaims(self):
        locks = _ChatLocks()
        async with locks.hold(10, 1):
            with self.assertRaises(TimeoutError):
                async with asyncio.timeout(0.01):
                    async with locks.hold(10, 1):
                        self.fail("waiter must not enter")
            self.assertEqual(locks._locks[(10, 1)].references, 1)
        self.assertEqual(locks._locks, {})

    async def test_exception_releases_entry(self):
        locks = _ChatLocks()
        with self.assertRaisesRegex(RuntimeError, "boom"):
            async with locks.hold(10, 1):
                raise RuntimeError("boom")
        self.assertEqual(locks._locks, {})

    async def test_release_race_reuses_entry_for_waiter(self):
        locks = _ChatLocks()
        started = asyncio.Event()
        release = asyncio.Event()

        async def owner():
            async with locks.hold(10, 1):
                started.set()
                await release.wait()

        owner_task = asyncio.create_task(owner())
        await started.wait()
        entry = locks._locks[(10, 1)]
        waiter = asyncio.create_task(self._acquire(locks, 10, 1))
        await asyncio.sleep(0)
        self.assertIs(locks._locks[(10, 1)], entry)
        self.assertEqual(entry.references, 2)
        release.set()
        new_arrival = asyncio.create_task(self._acquire(locks, 10, 1))
        await asyncio.gather(owner_task, waiter, new_arrival)
        self.assertEqual(locks._locks, {})

    async def test_thousands_of_topics_do_not_accumulate(self):
        locks = _ChatLocks()
        for topic in range(3000):
            async with locks.hold(10, topic):
                self.assertEqual(len(locks._locks), 1)
        self.assertEqual(locks._locks, {})

    async def test_new_request_after_reclamation_gets_new_entry(self):
        locks = _ChatLocks()
        async with locks.hold(10, 1):
            original = locks._locks[(10, 1)]
        self.assertEqual(locks._locks, {})
        async with locks.hold(10, 1):
            self.assertIsNot(locks._locks[(10, 1)], original)
        self.assertEqual(locks._locks, {})

    @staticmethod
    async def _acquire(locks: _ChatLocks, chat_id: int, topic_id: int):
        async with locks.hold(chat_id, topic_id):
            await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
