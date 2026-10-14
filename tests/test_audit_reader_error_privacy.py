"""Regression tests for the generic URL reader's tool-visible error boundary."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.search import reader


_SECRET = "credential=VERY_PRIVATE_SENTINEL"


class ReaderErrorPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_direct_fetch_exception_does_not_reach_tool_or_logs(self):
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.host == "r.jina.ai":
                return httpx.Response(404)
            raise RuntimeError(f"socket failed {_SECRET}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertLogs("app.search.reader", level="DEBUG") as logs:
                result = await reader.read_page("https://example.org/private", client=client)

        self.assertTrue(result.startswith("Không tải được trang:"))
        self.assertNotIn(_SECRET, result)
        self.assertNotIn(_SECRET, " ".join(logs.output))

    async def test_httpx_exception_does_not_reach_tool_or_logs(self):
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.host == "r.jina.ai":
                return httpx.Response(404)
            raise httpx.ConnectError(f"transport failed {_SECRET}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertLogs("app.search.reader", level="DEBUG") as logs:
                result = await reader.read_page("https://example.org/private", client=client)

        self.assertNotIn(_SECRET, result)
        self.assertNotIn(_SECRET, " ".join(logs.output))

    async def test_html_parser_exception_does_not_reach_tool_result(self):
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.host == "r.jina.ai":
                return httpx.Response(404)
            return httpx.Response(200, text="<html>hello</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with patch.object(reader, "_extract_html", side_effect=ValueError(_SECRET)):
                result = await reader.read_page("https://example.org/private", client=client)

        self.assertNotIn(_SECRET, result)
        self.assertTrue(result.startswith("Không tải được trang:"))


if __name__ == "__main__":
    unittest.main()
