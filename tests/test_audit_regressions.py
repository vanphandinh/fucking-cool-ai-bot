"""Offline regression tests for the September 2026 audit.

Run with: python -m unittest discover -s tests -p 'test_*.py' -v
External I/O is replaced at the transport/provider boundary.
"""

import asyncio
import gzip
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.ai.base import (
    AllProvidersFailed,
    ChatResponse,
    OpenAICompatProvider,
    ToolCall,
)
from app.ai.router import AIProviderRouter
from app.config import Settings
from app.search import reader


TOOLS = [{"type": "function", "function": {"name": "web_search"}}]


class RouterBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_error_does_not_permanently_disable_tools(self):
        sent_tools = []

        def respond(request):
            import json

            payload = json.loads(request.content)
            sent_tools.append(bool(payload.get("tools")))
            if len(sent_tools) == 1:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "Invalid tool call generated",
                            "code": "tool_use_failed",
                            "failed_generation": "tools are not supported for this model",
                        }
                    },
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "answer"}}]},
            )

        provider = OpenAICompatProvider(
            "bai-test",
            "https://example.org/v1",
            "fake",
            "fake",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.org/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            router = AIProviderRouter([provider])
            for _ in range(2):
                text, _ = await router.complete(
                    [{"role": "user", "content": "question"}],
                    TOOLS,
                    AsyncMock(),
                )
                self.assertEqual(text, "answer")
            self.assertEqual(sent_tools, [True, False, True])
            self.assertTrue(provider.supports_tools)
        finally:
            await provider.aclose()

    async def test_plain_pass_does_not_execute_unsolicited_tools(self):
        executed = []

        class IgnoresToolsFlag:
            name = "ignores-flag"
            supports_tools = False

            async def chat(self, messages, tools):
                return ChatResponse(tool_calls=[ToolCall("c1", "web_search", {})])

        async def execute(name, args):
            executed.append(name)
            return "search result"

        router = AIProviderRouter([IgnoresToolsFlag()])  # type: ignore[list-item]
        with self.assertRaises(AllProvidersFailed):
            await router.complete(
                [{"role": "user", "content": "question"}],
                TOOLS,
                execute,
            )
        self.assertEqual(executed, [])


class ReaderSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_html_chunk_boundary_does_not_split_words(self):
        body = b"<!--" + b"x" * 8181 + b"--><p>hello world</p>"
        self.assertEqual(await reader._extract_html(body), "hello world")

    async def test_html_ignores_nested_navigation_but_keeps_entities_and_unicode(self):
        body = (
            "<p>Chào &amp; bạn</p><nav>hidden<nav>nested</nav>hidden</nav>"
            "<script>bad</script><style>bad</style><p>đọc <b>tiếp</b></p>"
        ).encode()
        self.assertEqual(await reader._extract_html(body), "Chào & bạn đọc tiếp")

    async def test_html_extraction_yields_to_other_chats(self):
        heartbeat = asyncio.Event()

        class Page(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"<b>x</b><nav>x</nav>" * 10000

        def respond(request):
            if request.url.host == "r.jina.ai":
                return httpx.Response(404)
            asyncio.get_running_loop().call_soon(heartbeat.set)
            return httpx.Response(200, stream=Page())

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await reader.read_page(
                "https://example.org",
                timeout=0.02,
                client=client,
            )
        self.assertTrue(heartbeat.is_set(), "HTML parsing must not block other chat tasks")
        self.assertTrue(result.startswith("Không tải được trang") or result.startswith("x x"))

    async def test_non_unicast_and_translation_addresses_are_blocked_everywhere(self):
        transport = AsyncMock()
        backend = reader.SSRFCheckBackend(transport)
        for address in ("224.0.0.1", "ff02::1", "64:ff9b::7f00:1"):
            with self.subTest(address=address):
                host = f"[{address}]" if ":" in address else address
                self.assertIsNotNone(reader.validate_public_url(f"http://{host}/"))
                self.assertFalse(reader._is_public_ip(address))
                with self.assertRaises(reader.SSRFBlocked):
                    await backend.connect_tcp(address, 80)
        transport.connect_tcp.assert_not_awaited()

    async def test_page_timeout_is_total_deadline(self):
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                for _ in range(8):
                    await asyncio.sleep(0.01)
                    yield b"x"

        def respond(request):
            return httpx.Response(200, stream=SlowBody())

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await reader.read_page(
                "https://example.org",
                timeout=0.025,
                client=client,
            )
        self.assertTrue(result.startswith("Không tải được trang"), result)

    async def test_compressed_page_rejected_before_decoding(self):
        class CompressedBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield gzip.compress(b"a" * 1000)

        def respond(request):
            return httpx.Response(
                200,
                headers={"Content-Encoding": "gzip"},
                stream=CompressedBody(),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(reader._FetchError):
                await reader._fetch_limited(client, "https://example.org")


class HandlerDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_releases_chat_and_does_not_remember_unsent_answer(self):
        from types import SimpleNamespace

        from app.bot.handlers import _ChatLocks, _handle_question
        from app.core.context import ChatMemory
        from app.core.orchestrator import Answer
        from app.core.rate_limiter import RateLimiter
        from app.core.stats import Stats

        memory, locks, stats = ChatMemory(), _ChatLocks(), Stats()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=-100200),
            message_thread_id=None,
            bot=SimpleNamespace(send_chat_action=AsyncMock()),
            reply=AsyncMock(),
        )

        async def slow_answer(**kwargs):
            await asyncio.sleep(0.08)
            return Answer(text="late answer", provider="bai")

        settings = Settings(_env_file=None, question_timeout_sec=0.02)
        await _handle_question(
            message,
            "question",
            None,
            settings,
            SimpleNamespace(ask=slow_answer),
            memory,
            RateLimiter(),
            stats,
            locks,
        )
        self.assertEqual(memory.history_for(-100200), [])
        self.assertFalse((await locks.get(-100200)).locked())
        self.assertIn("quá thời gian", message.reply.call_args.args[0])

    async def test_timeout_includes_waiting_for_chat_lock(self):
        from types import SimpleNamespace

        from app.bot.handlers import _ChatLocks, _handle_question
        from app.core.context import ChatMemory
        from app.core.orchestrator import Answer
        from app.core.rate_limiter import RateLimiter
        from app.core.stats import Stats

        locks = _ChatLocks()
        lock = await locks.get(-100200)
        await lock.acquire()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
            chat=SimpleNamespace(id=-100200),
            message_thread_id=None,
            bot=SimpleNamespace(send_chat_action=AsyncMock()),
            reply=AsyncMock(),
        )
        answer = Answer("answer", "bai")
        orchestrator = SimpleNamespace(ask=AsyncMock(return_value=answer))
        task = asyncio.create_task(
            _handle_question(
                message,
                "q",
                None,
                Settings(_env_file=None, question_timeout_sec=0.02),
                orchestrator,
                ChatMemory(),
                RateLimiter(),
                Stats(),
                locks,
            )
        )
        try:
            done, _ = await asyncio.wait({task}, timeout=0.06)
            self.assertIn(task, done, "queued questions must expire too")
            await task
            orchestrator.ask.assert_not_awaited()
        finally:
            lock.release()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class StartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_bai_text_backend_fails_before_bot_creation(self):
        from app import main

        with patch.object(main, "Bot") as bot_cls:
            code = await main._amain(
                Settings(_env_file=None, bot_token="123:test")
            )
        self.assertEqual(code, 1)
        bot_cls.assert_not_called()

    async def test_restart_preserves_pending_telegram_updates(self):
        from types import SimpleNamespace

        from app import main

        bot = SimpleNamespace(
            get_me=AsyncMock(
                return_value=SimpleNamespace(username="testbot", first_name="Test")
            ),
            delete_webhook=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        provider = SimpleNamespace(name="bai", aclose=AsyncMock())
        with (
            patch.object(main, "Bot", return_value=bot),
            patch.object(
                main,
                "build_provider_router",
                return_value=SimpleNamespace(providers=[provider]),
            ),
            patch.object(main.Dispatcher, "start_polling", new=AsyncMock()),
        ):
            code = await main._amain(
                Settings(_env_file=None, bot_token="123:test", bai_api_key="k")
            )
        self.assertEqual(code, 0)
        drop_pending = bot.delete_webhook.call_args.kwargs.get("drop_pending_updates", False)
        self.assertFalse(drop_pending)
        provider.aclose.assert_awaited_once()

    async def test_unexpected_startup_failure_closes_all_clients(self):
        from types import SimpleNamespace

        from app import main

        bot = SimpleNamespace(
            get_me=AsyncMock(side_effect=RuntimeError("startup failed")),
            session=SimpleNamespace(close=AsyncMock()),
        )
        provider = SimpleNamespace(name="bai", aclose=AsyncMock())
        with (
            patch.object(main, "Bot", return_value=bot),
            patch.object(
                main,
                "build_provider_router",
                return_value=SimpleNamespace(providers=[provider]),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                await main._amain(
                    Settings(
                        _env_file=None,
                        bot_token="123:test",
                        bai_api_key="k",
                    )
                )
        bot.session.close.assert_awaited_once()
        provider.aclose.assert_awaited_once()

    async def test_shutdown_finishes_active_handlers_before_closing_providers(self):
        from types import SimpleNamespace

        from app import main

        events = []
        started = asyncio.Event()

        async def active_answer():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("handler stopped")

        async def polling(dispatcher, *bots, **kwargs):
            task = asyncio.create_task(active_answer())
            dispatcher._handle_update_tasks.add(task)
            task.add_done_callback(dispatcher._handle_update_tasks.discard)
            await started.wait()

        async def close_provider():
            events.append("provider closed")

        bot = SimpleNamespace(
            get_me=AsyncMock(
                return_value=SimpleNamespace(username="testbot", first_name="Test")
            ),
            delete_webhook=AsyncMock(),
            session=SimpleNamespace(close=AsyncMock()),
        )
        provider = SimpleNamespace(name="bai", aclose=close_provider)
        with (
            patch.object(main, "Bot", return_value=bot),
            patch.object(
                main,
                "build_provider_router",
                return_value=SimpleNamespace(providers=[provider]),
            ),
            patch.object(main.Dispatcher, "start_polling", new=polling),
        ):
            settings = Settings(
                _env_file=None,
                bot_token="123:test",
                bai_api_key="k",
            )
            await main._amain(settings)
        self.assertEqual(events, ["handler stopped", "provider closed"])


if __name__ == "__main__":
    unittest.main()
