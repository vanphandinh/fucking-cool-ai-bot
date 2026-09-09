from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.config import Settings
from app.search.crawl4ai_client import (
    Crawl4AIReadResult,
    close_crawl4ai_client,
    read_page,
)


class _AsyncResponseContext:
    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self) -> httpx.Response:
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _RaisingAsyncContext:
    def __init__(self, error: BaseException):
        self.error = error

    async def __aenter__(self):
        raise self.error

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _stream_client(response: httpx.Response) -> MagicMock:
    client = MagicMock()
    client.stream.return_value = _AsyncResponseContext(response)
    return client


def _raising_stream_client(error: BaseException) -> MagicMock:
    client = MagicMock()
    client.stream.return_value = _RaisingAsyncContext(error)
    return client


class Crawl4AIClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await close_crawl4ai_client()

    def settings(self, **overrides) -> Settings:
        values = {
            "crawl4ai_api_token": "secret-test-token",
            "crawl4ai_max_chars": 1000,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    async def test_prefers_fit_markdown_and_truncates(self):
        fit = "x" * 1200
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "http://crawl4ai:11235/crawl"),
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "url": "https://example.org/article",
                        "redirected_url": "https://example.org/article",
                        "markdown": {
                            "fit_markdown": fit,
                            "raw_markdown": "raw body",
                        },
                    }
                ],
            },
        )
        client = _stream_client(response)
        with patch(
            "app.search.crawl4ai_client._get_client",
            new=AsyncMock(return_value=client),
        ):
            result = await read_page("https://example.org/article", self.settings())
        self.assertIsInstance(result, Crawl4AIReadResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, fit[:1000])
        self.assertEqual(result.source_url, "https://example.org/article")
        headers = client.stream.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer secret-test-token")
        payload = client.stream.call_args.kwargs["json"]
        self.assertEqual(payload["urls"], ["https://example.org/article"])
        self.assertEqual(payload["browser_config"], {})
        self.assertEqual(payload["crawler_config"], {})

    async def test_oversized_response_is_rejected_before_json_use(self):
        huge_markdown = "x" * (4 * 1024 * 1024 + 1)
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "http://crawl4ai:11235/crawl"),
            json={
                "success": True,
                "results": [{"success": True, "markdown": huge_markdown}],
            },
        )
        response.headers.pop("content-length", None)
        client = _stream_client(response)
        client.post = AsyncMock()
        with patch(
            "app.search.crawl4ai_client._get_client",
            new=AsyncMock(return_value=client),
        ):
            result = await read_page("https://example.org", self.settings())
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "response_too_large")
        client.stream.assert_called_once()
        client.post.assert_not_awaited()

    async def test_raw_markdown_fallback(self):
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "http://crawl4ai:11235/crawl"),
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "markdown": {"fit_markdown": "", "raw_markdown": "raw body"},
                    }
                ],
            },
        )
        client = _stream_client(response)
        with patch(
            "app.search.crawl4ai_client._get_client",
            new=AsyncMock(return_value=client),
        ):
            result = await read_page(
                "https://example.org",
                self.settings(crawl4ai_max_chars=12000),
            )
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "raw body")

    async def test_cleaned_html_fallback(self):
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "http://crawl4ai:11235/crawl"),
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "markdown": {},
                        "cleaned_html": "<main><h1>Title</h1><p>Body</p></main>",
                    }
                ],
            },
        )
        client = _stream_client(response)
        with patch(
            "app.search.crawl4ai_client._get_client",
            new=AsyncMock(return_value=client),
        ):
            result = await read_page(
                "https://example.org",
                self.settings(crawl4ai_max_chars=12000),
            )
        self.assertTrue(result.ok)
        self.assertIn("Title", result.text)
        self.assertIn("Body", result.text)
        self.assertNotIn("<main>", result.text)

    async def test_failure_shapes_are_normalized(self):
        cases = [
            ({"success": True, "results": "not-a-list"}, "malformed_response"),
            (
                {
                    "success": True,
                    "results": [{"success": False, "error_message": "blocked"}],
                },
                "crawl_failed",
            ),
            (
                {"success": True, "results": [{"success": True, "markdown": {}}]},
                "empty_content",
            ),
        ]
        for body, reason in cases:
            with self.subTest(reason=reason):
                response = httpx.Response(
                    200,
                    request=httpx.Request("POST", "http://crawl4ai:11235/crawl"),
                    json=body,
                )
                client = _stream_client(response)
                with patch(
                    "app.search.crawl4ai_client._get_client",
                    new=AsyncMock(return_value=client),
                ):
                    result = await read_page("https://example.org", self.settings())
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, reason)
                self.assertNotIn("secret-test-token", result.reason)

    async def test_status_timeout_network_and_malformed_json_are_normalized(self):
        for status, expected in (
            (401, "auth"),
            (403, "auth"),
            (429, "rate_limited"),
            (503, "upstream_5xx"),
        ):
            with self.subTest(status=status):
                response = httpx.Response(
                    status,
                    request=httpx.Request("POST", "http://crawl4ai:11235/crawl"),
                )
                client = _stream_client(response)
                with patch(
                    "app.search.crawl4ai_client._get_client",
                    new=AsyncMock(return_value=client),
                ):
                    result = await read_page("https://example.org", self.settings())
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, expected)

        for effect, expected in (
            (httpx.ReadTimeout("boom"), "timeout"),
            (httpx.ConnectError("boom"), "network"),
        ):
            with self.subTest(expected=expected):
                client = _raising_stream_client(effect)
                with patch(
                    "app.search.crawl4ai_client._get_client",
                    new=AsyncMock(return_value=client),
                ):
                    result = await read_page("https://example.org", self.settings())
                self.assertEqual(result.reason, expected)

        response = httpx.Response(
            200,
            request=httpx.Request("POST", "http://crawl4ai:11235/crawl"),
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
        client = _stream_client(response)
        with patch(
            "app.search.crawl4ai_client._get_client",
            new=AsyncMock(return_value=client),
        ):
            malformed = await read_page("https://example.org", self.settings())
        self.assertEqual(malformed.reason, "malformed_json")

    async def test_private_redirect_is_not_trusted(self):
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "http://crawl4ai:11235/crawl"),
            json={
                "success": True,
                "results": [
                    {
                        "success": True,
                        "redirected_url": "http://127.0.0.1/private",
                        "markdown": "body",
                    }
                ],
            },
        )
        client = _stream_client(response)
        with patch(
            "app.search.crawl4ai_client._get_client",
            new=AsyncMock(return_value=client),
        ):
            result = await read_page("https://example.org", self.settings())
        self.assertTrue(result.ok)
        self.assertEqual(result.source_url, "https://example.org")

    async def test_cancelled_error_propagates(self):
        client = _raising_stream_client(asyncio.CancelledError())
        with patch(
            "app.search.crawl4ai_client._get_client",
            new=AsyncMock(return_value=client),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await read_page("https://example.org", self.settings())


if __name__ == "__main__":
    unittest.main()
