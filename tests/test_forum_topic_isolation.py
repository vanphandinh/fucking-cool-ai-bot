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


if __name__ == "__main__":
    unittest.main()
