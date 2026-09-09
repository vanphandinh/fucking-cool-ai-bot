from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.core.orchestrator import Orchestrator
from app.search.url_service import UrlReadResult


class _EquivalentUrlRouter:
    last_fallbacks = 0

    async def complete(self, messages, tools, tool_executor, **kwargs):
        first = await tool_executor(
            "fetch_url",
            {"url": "https://example.org/article?utm_source=tool#section", "mode": "auto"},
        )
        second = await tool_executor(
            "fetch_url",
            {"url": "https://EXAMPLE.org:443/article/", "mode": "auto"},
        )
        self.outputs = [first, second]
        return "done", "fake"


class _ConcurrentEquivalentUrlRouter:
    last_fallbacks = 0

    async def complete(self, messages, tools, tool_executor, **kwargs):
        self.outputs = await asyncio.gather(
            tool_executor(
                "fetch_url",
                {"url": "https://example.org/article?utm_source=tool#section", "mode": "auto"},
            ),
            tool_executor(
                "fetch_url",
                {"url": "https://EXAMPLE.org:443/article/", "mode": "auto"},
            ),
        )
        return "done", "fake"


class ToolUrlDedupeTests(unittest.IsolatedAsyncioTestCase):
    async def test_equivalent_urls_share_one_fetch_cache_entry(self):
        router = _EquivalentUrlRouter()
        read = AsyncMock(
            return_value=UrlReadResult(
                "rendered body",
                "https://example.org/article",
                "crawl4ai",
                True,
            )
        )

        with patch("app.core.orchestrator.url_service.read_url", new=read):
            answer = await Orchestrator(Settings(_env_file=None), router).ask("đọc nguồn")

        read.assert_awaited_once()
        self.assertEqual(router.outputs[0], router.outputs[1])
        self.assertTrue(answer.searched)
        self.assertEqual(len(answer.sources), 1)
        self.assertEqual(answer.sources[0]["url"], "https://example.org/article")

    async def test_concurrent_equivalent_urls_share_one_inflight_fetch(self):
        router = _ConcurrentEquivalentUrlRouter()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def read_url(url, settings, mode):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return UrlReadResult(
                "rendered body",
                "https://example.org/article",
                "crawl4ai",
                True,
            )

        async def run_ask():
            with patch("app.core.orchestrator.url_service.read_url", new=read_url):
                return await Orchestrator(Settings(_env_file=None), router).ask("đọc nguồn")

        task = asyncio.create_task(run_ask())
        await started.wait()
        await asyncio.sleep(0)
        self.assertEqual(calls, 1)
        release.set()
        answer = await task

        self.assertEqual(calls, 1)
        self.assertEqual(router.outputs[0], router.outputs[1])
        self.assertTrue(answer.searched)
        self.assertEqual(len(answer.sources), 1)


if __name__ == "__main__":
    unittest.main()
