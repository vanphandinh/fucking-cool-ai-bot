from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.search.url_service import read_url
from app.search.x_reader import XReadResult


class UrlServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_crawl4ai_defaults(self):
        settings = Settings(_env_file=None)
        self.assertTrue(settings.crawl4ai_enabled)
        self.assertEqual(settings.crawl4ai_url, "http://crawl4ai:11235")
        self.assertEqual(settings.crawl4ai_timeout_sec, 25.0)
        self.assertEqual(settings.crawl4ai_max_chars, 12000)

    def test_crawl4ai_timeout_must_be_below_question_timeout(self):
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                crawl4ai_timeout_sec=60,
                question_timeout_sec=30,
            )

    async def test_x_status_prefers_specialized_reader(self):
        specialized = AsyncMock(
            return_value=XReadResult(
                "post text",
                "fxtwitter_status",
                "https://x.com/a/status/1234567890",
                False,
            )
        )
        generic = AsyncMock(return_value="generic")
        settings = Settings(_env_file=None, x_fetch_enabled=True)
        with (
            patch("app.search.url_service.x_reader.read_x_url", new=specialized),
            patch("app.search.url_service.reader.read_page", new=generic),
        ):
            result = await read_url("https://x.com/a/status/1234567890", settings)
        self.assertTrue(result.ok)
        self.assertEqual(result.backend, "fxtwitter_status")
        specialized.assert_awaited_once()
        generic.assert_not_awaited()

    async def test_specialized_failure_falls_back_to_canonical_x_url(self):
        specialized = AsyncMock(return_value=None)
        generic = AsyncMock(return_value="generic X page")
        settings = Settings(_env_file=None, x_fetch_enabled=True)
        with (
            patch("app.search.url_service.x_reader.read_x_url", new=specialized),
            patch("app.search.url_service.reader.read_page", new=generic),
        ):
            result = await read_url("https://fxtwitter.com/a/status/1234567890", settings)
        self.assertEqual(result.backend, "generic_reader")
        generic.assert_awaited_once_with(
            "https://x.com/a/status/1234567890",
            timeout=settings.request_timeout_sec,
        )

    async def test_feature_flag_bypasses_specialized_reader(self):
        specialized = AsyncMock()
        generic = AsyncMock(return_value="generic")
        settings = Settings(_env_file=None, x_fetch_enabled=False)
        with (
            patch("app.search.url_service.x_reader.read_x_url", new=specialized),
            patch("app.search.url_service.reader.read_page", new=generic),
        ):
            result = await read_url("https://x.com/a/status/1234567890", settings)
        specialized.assert_not_awaited()
        generic.assert_awaited_once()
        self.assertTrue(result.ok)

    async def test_x_thread_mode_is_rejected_for_non_x_url(self):
        result = await read_url(
            "https://example.org/article",
            Settings(_env_file=None),
            mode="x_thread",
        )
        self.assertFalse(result.ok)
        self.assertIn("x_thread", result.text)

    async def test_ssrf_invalid_url_is_blocked_before_network(self):
        result = await read_url("http://127.0.0.1/private", Settings(_env_file=None))
        self.assertFalse(result.ok)
        self.assertIn("Không thể tải trang", result.text)

    async def test_generic_failure_is_not_marked_ok(self):
        settings = Settings(_env_file=None, x_fetch_enabled=False)
        with patch(
            "app.search.url_service.reader.read_page",
            new=AsyncMock(return_value="Không tải được trang: HTTP 503"),
        ):
            result = await read_url("https://x.com/a/status/1234567890", settings)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
