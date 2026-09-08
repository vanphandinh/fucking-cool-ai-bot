import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.exceptions import TelegramBadRequest

from app.bot.handlers import _send_answer_parts


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
            ok = await _send_answer_parts(
                message,
                ["<b>Một</b>", "<i>Hai</i>"],
            )
            self.assertTrue(ok)
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
            ok = await _send_answer_parts(message, ["<b>Hello</b>"])
            self.assertTrue(ok)
            second_call = message.reply.await_args_list[1]
            self.assertEqual(second_call.args[0], "Hello")
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
            ok = await _send_answer_parts(
                message,
                ["<b>Một</b>", "<i>Hai</i>"],
            )
            self.assertTrue(ok)
            fallback = bot.send_message.await_args_list[1]
            self.assertEqual(fallback.kwargs["text"], "Hai")
            self.assertEqual(fallback.kwargs["message_thread_id"], 42)
            self.assertNotIn("parse_mode", fallback.kwargs)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
