"""Failure injection at the legacy Telegram/log boundary."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.base import AllProvidersFailed
from app.bot.handlers import _ChatLocks, _handle_question, _send_answer_parts
from app.bot.image_results import send_image_results
from app.config import Settings
from app.core.context import ChatMemory
from app.core.rate_limiter import RateLimiter
from app.core.stats import Stats


SECRET = "SENTINEL_PRIVATE_PROVIDER_TOKEN_987"


class LegacyLogPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def _failure(self, exception):
        async def ask(**kwargs):
            raise exception

        stats = Stats()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=3),
            chat=SimpleNamespace(id=-200),
            message_thread_id=None,
            bot=SimpleNamespace(send_chat_action=AsyncMock()),
            reply=AsyncMock(),
        )
        with self.assertLogs("app.bot.handlers", level="ERROR") as logs:
            await _handle_question(
                message, "hello", None,
                Settings(_env_file=None, question_timeout_sec=2),
                SimpleNamespace(ask=ask),
                ChatMemory(), RateLimiter(10), stats, _ChatLocks(),
                SimpleNamespace(load=AsyncMock(return_value=())),
            )
        self.assertTrue(message.reply.await_count)
        self.assertNotIn(SECRET, "\n".join(logs.output))
        self.assertNotIn(SECRET, stats.last_error or "")

    async def test_provider_failure_payload_not_logged_or_stored(self):
        await self._failure(AllProvidersFailed(f"upstream: {SECRET}"))

    async def test_unexpected_failure_payload_not_logged_or_stored(self):
        await self._failure(RuntimeError(f"upstream: {SECRET}"))

    async def test_failed_image_url_not_logged(self):
        bot = SimpleNamespace(send_photo=AsyncMock(side_effect=RuntimeError(SECRET)))
        with self.assertLogs("app.bot.image_results", level="INFO") as logs:
            sent = await send_image_results(bot, -200, [{"image_url": "https://example.org/i"}])
        self.assertEqual(sent, 0)
        self.assertNotIn(SECRET, "\n".join(logs.output))

    async def test_first_telegram_send_error_does_not_log_private_payload(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock()),
            chat=SimpleNamespace(id=-200),
            message_thread_id=None,
            reply=AsyncMock(side_effect=RuntimeError(SECRET)),
        )
        with self.assertLogs("app.bot.handlers", level="ERROR") as logs:
            delivered = await _send_answer_parts(message, ["first"])
        self.assertFalse(delivered)
        self.assertNotIn(SECRET, "\n".join(logs.output))

    async def test_followup_telegram_send_error_does_not_log_private_payload(self):
        message = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError(SECRET))),
            chat=SimpleNamespace(id=-200),
            message_thread_id=None,
            reply=AsyncMock(),
        )
        with self.assertLogs("app.bot.handlers", level="ERROR") as logs:
            delivered = await _send_answer_parts(message, ["first", "second"])
        self.assertFalse(delivered)
        self.assertNotIn(SECRET, "\n".join(logs.output))

    async def test_partial_delivery_does_not_log_private_payload(self):
        memory = ChatMemory()
        stats = Stats()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=3),
            chat=SimpleNamespace(id=-200),
            message_thread_id=None,
            bot=SimpleNamespace(
                send_chat_action=AsyncMock(),
                send_message=AsyncMock(side_effect=RuntimeError(SECRET)),
            ),
            reply=AsyncMock(),
        )
        answer = SimpleNamespace(
            text="A" * 5000,
            provider="test",
            fallbacks=(),
            searched=False,
            sources=[],
            images=[],
        )
        with self.assertLogs("app.bot.handlers", level="ERROR") as logs:
            await _handle_question(
                message,
                "hello",
                None,
                Settings(_env_file=None, question_timeout_sec=2),
                SimpleNamespace(ask=AsyncMock(return_value=answer)),
                memory,
                RateLimiter(10),
                stats,
                _ChatLocks(),
                SimpleNamespace(load=AsyncMock(return_value=())),
            )
        self.assertNotIn(SECRET, "\\n".join(logs.output))
        self.assertEqual(stats.partial_deliveries, 1)
        self.assertEqual(stats.by_provider["test"], 0)
        self.assertEqual(len(memory.history_for(-200)), 2)

    async def test_optional_image_delivery_error_does_not_log_private_payload(self):
        answer = SimpleNamespace(
            text="hello", provider="test", searched=False,
            images=[{"image_url": "https://example.org/image.png"}],
            fallbacks=(),
        )
        message = SimpleNamespace(
            bot=SimpleNamespace(send_message=AsyncMock(), send_chat_action=AsyncMock()),
            from_user=SimpleNamespace(id=3),
            chat=SimpleNamespace(id=-200),
            message_thread_id=None,
            reply=AsyncMock(),
        )
        with (
            patch(
                "app.bot.handlers.send_image_results",
                new=AsyncMock(side_effect=RuntimeError(SECRET)),
            ),
            self.assertLogs("app.bot.handlers", level="ERROR") as logs,
        ):
            await _handle_question(
                message, "hello", None,
                Settings(_env_file=None, question_timeout_sec=2),
                SimpleNamespace(ask=AsyncMock(return_value=answer)),
                ChatMemory(), RateLimiter(10), Stats(), _ChatLocks(),
                SimpleNamespace(load=AsyncMock(return_value=())),
            )
        self.assertNotIn(SECRET, "\n".join(logs.output))

    async def test_startup_validation_error_does_not_log_private_payload(self):
        import importlib

        entrypoint = importlib.import_module("app.main")
        with (
            patch.object(entrypoint, "get_settings", side_effect=RuntimeError(SECRET)),
            self.assertLogs("app.main", level="ERROR") as logs,
            self.assertRaises(SystemExit),
        ):
            entrypoint.main()
        self.assertNotIn(SECRET, "\n".join(logs.output))
