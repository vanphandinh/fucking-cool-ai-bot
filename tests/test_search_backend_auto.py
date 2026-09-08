"""Regression tests for the unified free search backend policy."""

from __future__ import annotations

import importlib.util
import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.search import image_service, service


class UnifiedSearchConfigTests(unittest.TestCase):
    def test_search_backend_defaults_to_auto_and_tavily_is_removed(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.search_backend, "auto")
        self.assertFalse(hasattr(settings, "image_search_backend"))
        self.assertFalse(hasattr(settings, "tavily_api_key"))
        self.assertIsNone(importlib.util.find_spec("app.search.tavily_backend"))

    def test_only_auto_searxng_and_ddgs_are_valid(self) -> None:
        for backend in ("auto", "searxng", "ddgs"):
            with self.subTest(backend=backend):
                self.assertEqual(Settings(_env_file=None, search_backend=backend).search_backend, backend)
        with self.assertRaises(ValueError):
            Settings(_env_file=None, search_backend="tavily")


class UnifiedTextSearchRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_prefers_searxng_and_does_not_call_ddgs_when_results_exist(self) -> None:
        searx = AsyncMock(return_value=[{"title": "x", "url": "https://x", "snippet": ""}])
        ddgs = AsyncMock(return_value=[])
        settings = Settings(_env_file=None, search_backend="auto", searxng_url="http://searxng:8080")
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search("x", settings)
        self.assertEqual(results[0]["title"], "x")
        searx.assert_awaited_once()
        ddgs.assert_not_awaited()

    async def test_auto_falls_back_to_ddgs_when_searxng_errors_or_is_empty(self) -> None:
        for searx_result in (RuntimeError("down"), []):
            with self.subTest(searx_result=repr(searx_result)):
                searx = (
                    AsyncMock(side_effect=searx_result)
                    if isinstance(searx_result, Exception)
                    else AsyncMock(return_value=searx_result)
                )
                ddgs = AsyncMock(
                    return_value=[{"title": "fallback", "url": "https://x", "snippet": ""}]
                )
                settings = Settings(
                    _env_file=None,
                    search_backend="auto",
                    searxng_url="http://searxng:8080",
                )
                with (
                    patch("app.search.searxng_backend.search_searxng", new=searx),
                    patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
                ):
                    results = await service.search("x", settings)
                self.assertEqual(results[0]["title"], "fallback")
                ddgs.assert_awaited_once()


class UnifiedImageSearchRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_search_uses_search_backend_not_a_separate_setting(self) -> None:
        ddgs = AsyncMock(return_value=[])
        settings = Settings(_env_file=None, search_backend="ddgs")
        with patch.object(image_service, "search_ddgs_images", new=ddgs):
            await image_service.search_images("x", settings)
        ddgs.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
