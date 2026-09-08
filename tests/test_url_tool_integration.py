from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.core.orchestrator import Orchestrator
from app.search.url_service import UrlReadResult


class _FetchTwiceRouter:
    last_fallbacks = 0

    def __init__(self):
        self.outputs = []

    async def complete(self, messages, tools, tool_executor, **kwargs):
        args = {"url": "https://x.com/a/status/1234567890", "mode": "auto"}
        self.outputs.append(await tool_executor("fetch_url", args))
        self.outputs.append(await tool_executor("fetch_url", args))
        return "done", "fake"


class _ThreadRouter:
    last_fallbacks = 0

    async def complete(self, messages, tools, tool_executor, **kwargs):
        await tool_executor(
            "fetch_url",
            {"url": "https://x.com/a/status/1234567890", "mode": "x_thread"},
        )
        return "thread done", "fake"


class _FailureRouter:
    last_fallbacks = 0

    async def complete(self, messages, tools, tool_executor, **kwargs):
        self.output = await tool_executor(
            "fetch_url", {"url": "https://x.com/a/status/1234567890"}
        )
        return "không đọc được post", "fake"


class UrlToolIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_x_url_is_fetched_once_per_question_and_source_is_canonical(self):
        router = _FetchTwiceRouter()
        read = AsyncMock(
            return_value=UrlReadResult(
                "post text",
                "https://x.com/a/status/1234567890",
                "fxtwitter_status",
                True,
            )
        )
        with patch("app.core.orchestrator.url_service.read_url", new=read):
            answer = await Orchestrator(Settings(_env_file=None), router).ask("đọc link này")
        read.assert_awaited_once()
        self.assertEqual(router.outputs[0], router.outputs[1])
        self.assertTrue(answer.searched)
        self.assertEqual(answer.sources[0]["url"], "https://x.com/a/status/1234567890")

    async def test_x_thread_mode_is_forwarded_to_url_service(self):
        read = AsyncMock(
            return_value=UrlReadResult(
                "thread text",
                "https://x.com/a/status/1234567890",
                "fxtwitter_thread",
                True,
            )
        )
        with patch("app.core.orchestrator.url_service.read_url", new=read):
            answer = await Orchestrator(Settings(_env_file=None), _ThreadRouter()).ask(
                "đọc cả thread"
            )
        self.assertEqual(answer.text, "thread done")
        self.assertEqual(read.await_args.args[2], "x_thread")

    async def test_failed_fetch_does_not_claim_search_or_source(self):
        router = _FailureRouter()
        read = AsyncMock(
            return_value=UrlReadResult(
                "Không tải được trang: HTTP 503",
                "https://x.com/a/status/1234567890",
                "generic_reader",
                False,
            )
        )
        with patch("app.core.orchestrator.url_service.read_url", new=read):
            answer = await Orchestrator(Settings(_env_file=None), router).ask("đọc link")
        self.assertFalse(answer.searched)
        self.assertEqual(answer.sources, [])
        self.assertIn("HTTP 503", router.output)


if __name__ == "__main__":
    unittest.main()
