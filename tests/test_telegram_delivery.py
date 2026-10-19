import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from app.bot.handlers import _ChatLocks, _handle_question, _send_answer_parts
from app.config import Settings
from app.core.context import ChatMemory
from app.core.rate_limiter import RateLimiter
from app.core.stats import Stats


class TelegramDeliveryTests(unittest.TestCase):
    def test_sends_all_parts_with_html_parse_mode(self):
        async def run():
            bot = SimpleNamespace(send_message=AsyncMock())
            message = SimpleNamespace(
                bot=bot,
                chat=SimpleNamespace(id=-1001),
                message_thread_id=None,
                reply=AsyncMock(),
            )
            acknowledged = []
            ok = await _send_answer_parts(
                message,
                ["<b>Một</b>", "<i>Hai</i>"],
                on_ack=acknowledged.append,
            )
            self.assertTrue(ok)
            self.assertEqual(acknowledged, ["Một", "Hai"])
            self.assertEqual(
                message.reply.await_args.kwargs["parse_mode"],
                "HTML",
            )
            self.assertEqual(
                bot.send_message.await_args.kwargs["parse_mode"],
                "HTML",
            )
            preview = message.reply.await_args.kwargs["link_preview_options"]
            self.assertTrue(preview.is_disabled)

        asyncio.run(run())

    def test_plain_parts_do_not_force_html_parse_mode(self):
        async def run():
            bot = SimpleNamespace(send_message=AsyncMock())
            message = SimpleNamespace(
                bot=bot,
                chat=SimpleNamespace(id=-1001),
                message_thread_id=None,
                reply=AsyncMock(),
            )
            acknowledged = []
            ok = await _send_answer_parts(
                message,
                ["Một &amp; hai", "Ba"],
                on_ack=acknowledged.append,
            )
            self.assertTrue(ok)
            self.assertEqual(acknowledged, ["Một & hai", "Ba"])
            first = message.reply.await_args
            second = bot.send_message.await_args
            self.assertEqual(first.args[0], "Một & hai")
            self.assertNotIn("parse_mode", first.kwargs)
            self.assertEqual(second.kwargs["text"], "Ba")
            self.assertNotIn("parse_mode", second.kwargs)

        asyncio.run(run())

    def test_first_part_falls_back_to_plain_on_bad_html(self):
        async def run():
            bot = SimpleNamespace(send_message=AsyncMock())
            message = SimpleNamespace(
                bot=bot,
                chat=SimpleNamespace(id=-1001),
                message_thread_id=None,
                reply=AsyncMock(
                    side_effect=[
                        TelegramBadRequest(
                            method=SimpleNamespace(),
                            message="Bad Request: can't parse entities",
                        ),
                        None,
                    ]
                ),
            )
            acknowledged = []
            ok = await _send_answer_parts(
                message, ["<b>Hello</b>"], on_ack=acknowledged.append
            )
            self.assertTrue(ok)
            second_call = message.reply.await_args_list[1]
            self.assertEqual(second_call.args[0], "Hello")
            self.assertEqual(acknowledged, ["Hello"])

            self.assertNotIn("parse_mode", second_call.kwargs)

        asyncio.run(run())

    def test_non_parse_bad_request_is_not_retried_as_plain(self):
        async def run():
            bot = SimpleNamespace(send_message=AsyncMock())
            message = SimpleNamespace(
                bot=bot,
                chat=SimpleNamespace(id=-1001),
                message_thread_id=None,
                reply=AsyncMock(
                    side_effect=TelegramBadRequest(
                        method=SimpleNamespace(),
                        message="Bad Request: chat not found",
                    )
                ),
            )
            ok = await _send_answer_parts(message, ["<b>Hello</b>"])
            self.assertFalse(ok)
            self.assertEqual(message.reply.await_count, 1)

        asyncio.run(run())

    def test_failed_followup_returns_only_acknowledged_prefix(self):
        async def run():
            bot = SimpleNamespace(
                send_message=AsyncMock(side_effect=RuntimeError("transport failed"))
            )
            message = SimpleNamespace(
                bot=bot,
                chat=SimpleNamespace(id=-1001),
                message_thread_id=42,
                reply=AsyncMock(),
            )
            acknowledged = []
            delivered = await _send_answer_parts(
                message,
                ["<b>first</b>", "second"],
                on_ack=acknowledged.append,
            )
            self.assertFalse(delivered)
            self.assertEqual(acknowledged, ["first"])
            self.assertEqual(message.reply.await_count, 1)
            self.assertEqual(bot.send_message.await_count, 1)
            self.assertEqual(
                bot.send_message.await_args.kwargs["message_thread_id"], 42
            )

        asyncio.run(run())

    def test_cancellation_after_first_ack_preserves_prefix(self):
        async def run():
            entered = asyncio.Event()
            bot = SimpleNamespace()

            async def send_message(**kwargs):
                entered.set()
                await asyncio.Event().wait()

            bot.send_message = AsyncMock(side_effect=send_message)
            message = SimpleNamespace(
                bot=bot,
                chat=SimpleNamespace(id=-1001),
                message_thread_id=None,
                reply=AsyncMock(),
            )
            acknowledged = []
            task = asyncio.create_task(
                _send_answer_parts(
                    message, ["first", "second"], on_ack=acknowledged.append
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(acknowledged, ["first"])

        asyncio.run(run())

    def test_followup_part_falls_back_to_plain_on_entity_error(self):
        async def run():
            bot = SimpleNamespace(
                send_message=AsyncMock(
                    side_effect=[
                        TelegramBadRequest(
                            method=SimpleNamespace(),
                            message="Bad Request: can't parse entities",
                        ),
                        None,
                    ]
                )
            )
            message = SimpleNamespace(
                bot=bot,
                chat=SimpleNamespace(id=-1001),
                message_thread_id=42,
                reply=AsyncMock(),
            )
            acknowledged = []
            ok = await _send_answer_parts(
                message,
                ["<b>Một</b>", "<i>Hai</i>"],
                on_ack=acknowledged.append,
            )
            self.assertTrue(ok)
            self.assertEqual(acknowledged, ["Một", "Hai"])
            fallback = bot.send_message.await_args_list[1]
            self.assertEqual(fallback.kwargs["text"], "Hai")
            self.assertEqual(fallback.kwargs["message_thread_id"], 42)
            self.assertNotIn("parse_mode", fallback.kwargs)

        asyncio.run(run())


class LegacyHandlerPartialDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_ack_commits_full_answer_before_real_cancellation(self):
        memory = ChatMemory()
        stats = Stats()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            chat=SimpleNamespace(id=-1001),
            message_thread_id=42,
            bot=SimpleNamespace(
                send_chat_action=AsyncMock(),
                send_message=AsyncMock(),
            ),
            reply=AsyncMock(),
        )
        answer = SimpleNamespace(
            text="A" * 5000,
            provider="test-provider",
            fallbacks=(),
            searched=True,
            sources=[],
            images=[],
        )
        original_push_exchange = memory.push_exchange

        def cancel_after_full_commit(key, user, assistant, *, order_key=None):
            original_push_exchange(key, user, assistant, order_key=order_key)
            asyncio.current_task().cancel()

        memory.push_exchange = cancel_after_full_commit
        task = asyncio.create_task(
            _handle_question(
                message,
                "question",
                None,
                Settings(_env_file=None, question_timeout_sec=2),
                SimpleNamespace(ask=AsyncMock(return_value=answer)),
                memory,
                RateLimiter(10),
                stats,
                _ChatLocks(),
                SimpleNamespace(load=AsyncMock(return_value=())),
            )
        )

        with self.assertRaises(asyncio.CancelledError):
            await task

        history = memory.history_for((-1001, 42))
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertEqual(history[0]["content"], "question")
        self.assertEqual(history[1]["content"], "A" * 4000)
        self.assertEqual(stats.partial_deliveries, 0)
        self.assertEqual(stats.by_provider["test-provider"], 1)
        self.assertEqual(stats.searches, 1)
        self.assertEqual(message.reply.await_count, 1)
        self.assertEqual(message.bot.send_message.await_count, 1)

    async def test_partial_send_commits_acknowledged_prefix_and_notifies_user(self):
        memory = ChatMemory()
        stats = Stats()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            chat=SimpleNamespace(id=-1001),
            message_thread_id=42,
            bot=SimpleNamespace(
                send_chat_action=AsyncMock(),
                send_message=AsyncMock(side_effect=RuntimeError("transport failed")),
            ),
            reply=AsyncMock(),
        )
        answer = SimpleNamespace(
            text="A" * 5000,
            provider="test-provider",
            fallbacks=(),
            searched=True,
            sources=[],
            images=[],
        )

        await _handle_question(
            message,
            "question",
            None,
            Settings(_env_file=None, question_timeout_sec=2),
            SimpleNamespace(ask=AsyncMock(return_value=answer)),
            memory,
            RateLimiter(10),
            stats,
            _ChatLocks(),
            SimpleNamespace(load=AsyncMock(return_value=())),
        )

        history = memory.history_for((-1001, 42))
        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertEqual(history[0]["content"], "question")
        marker = "\n[...] Câu trả lời chưa hoàn chỉnh"
        self.assertEqual(history[1]["content"], "A" * 3900 + marker)
        self.assertEqual(len(history[1]["content"]), 3900 + len(marker))
        self.assertEqual(stats.partial_deliveries, 1)
        self.assertEqual(stats.by_provider["test-provider"], 0)
        self.assertEqual(stats.searches, 0)
        self.assertEqual(stats.fallback_count, 0)
        self.assertEqual(message.bot.send_message.await_count, 1)
        self.assertEqual(
            message.bot.send_message.await_args.kwargs["message_thread_id"], 42
        )
        user_notices = [
            call.args[0]
            for call in message.reply.await_args_list[1:]
            if call.args
        ]
        self.assertTrue(any("một phần" in notice.lower() for notice in user_notices))


if __name__ == "__main__":
    unittest.main()
