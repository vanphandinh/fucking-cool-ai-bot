from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
