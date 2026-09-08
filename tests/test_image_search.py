"""Regression tests for Internet image search and Telegram delivery."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.core import orchestrator
from app.search import ddgs_backend, searxng_backend


class SearXNGImageSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_searxng_image_search_contract_and_normalization(self) -> None:
        self.assertTrue(
            hasattr(searxng_backend, "search_searxng_images"),
            "search_searxng_images must exist",
        )
        if not hasattr(searxng_backend, "search_searxng_images"):
            return

        seen_params: dict = {}

        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "results": [
                        {
                            "title": "Cat",
                            "url": "https://example.org/cat",
                            "img_src": "https://img.example.org/cat.jpg",
                            "thumbnail_src": "https://img.example.org/cat-thumb.jpg",
                            "source": "bing images",
                        },
                        {
                            "title": "duplicate",
                            "url": "https://example.org/dup",
                            "img_src": "https://img.example.org/cat.jpg",
                        },
                        {
                            "title": "bad",
                            "url": "https://example.org/bad",
                            "img_src": "file:///tmp/bad.jpg",
                        },
                    ]
                }

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, _url, *, params, headers):
                seen_params.update(params)
                return Response()

        with patch.object(searxng_backend.httpx, "AsyncClient", return_value=Client()):
            results = await searxng_backend.search_searxng_images(
                "cats",
                Settings(_env_file=None, searxng_url="http://searxng:8080"),
                4,
            )

        self.assertEqual(seen_params["categories"], "images")
        self.assertEqual(seen_params["format"], "json")
        self.assertEqual(seen_params["safesearch"], "1")
        self.assertEqual(
            results,
            [
                {
                    "title": "Cat",
                    "image_url": "https://img.example.org/cat.jpg",
                    "thumbnail_url": "https://img.example.org/cat-thumb.jpg",
                    "page_url": "https://example.org/cat",
                    "source": "bing images",
                }
            ],
        )


class DDGSImageSearchTests(unittest.TestCase):
    def test_ddgs_image_search_contract_and_normalization(self) -> None:
        self.assertTrue(
            hasattr(ddgs_backend, "_ddgs_image_search_sync"),
            "_ddgs_image_search_sync must exist",
        )
        if not hasattr(ddgs_backend, "_ddgs_image_search_sync"):
            return

        seen: dict = {}

        class FakeDDGS:
            def __init__(self, timeout=15):
                seen["timeout"] = timeout

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def images(self, query, *, max_results, region, safesearch):
                seen.update(
                    query=query,
                    max_results=max_results,
                    region=region,
                    safesearch=safesearch,
                )
                return [
                    {
                        "title": "Dog",
                        "image": "https://img.example.org/dog.jpg",
                        "thumbnail": "https://img.example.org/dog-thumb.jpg",
                        "url": "https://example.org/dog",
                        "source": "Bing",
                    }
                ]

        with patch.dict(sys.modules, {"ddgs": SimpleNamespace(DDGS=FakeDDGS)}):
            results = ddgs_backend._ddgs_image_search_sync("dogs", 3)

        self.assertEqual(seen["query"], "dogs")
        self.assertEqual(seen["max_results"], 3)
        self.assertEqual(seen["region"], "vn-vi")
        self.assertEqual(seen["safesearch"], "moderate")
        self.assertEqual(results[0]["image_url"], "https://img.example.org/dog.jpg")
        self.assertEqual(results[0]["page_url"], "https://example.org/dog")


class ImageSearchServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_backend_falls_back_from_searxng_to_ddgs(self) -> None:
        spec = importlib.util.find_spec("app.search.image_service")
        self.assertIsNotNone(spec, "app.search.image_service must exist")
        if spec is None:
            return
        image_service = importlib.import_module("app.search.image_service")
        searx = AsyncMock(side_effect=RuntimeError("searx down"))
        ddgs = AsyncMock(
            return_value=[
                {
                    "title": "fallback",
                    "image_url": "https://img.example.org/fallback.jpg",
                    "thumbnail_url": "",
                    "page_url": "https://example.org/fallback",
                    "source": "ddgs",
                }
            ]
        )
        settings = Settings(
            _env_file=None,
            searxng_url="http://searxng:8080",
            search_backend="auto",
            image_search_max_results=4,
        )
        with (
            patch.object(image_service, "search_searxng_images", new=searx),
            patch.object(image_service, "search_ddgs_images", new=ddgs),
        ):
            results = await image_service.search_images("x", settings)
        self.assertEqual(results[0]["title"], "fallback")
        searx.assert_awaited_once()
        ddgs.assert_awaited_once()

    async def test_auto_backend_does_not_call_ddgs_when_searxng_succeeds(self) -> None:
        spec = importlib.util.find_spec("app.search.image_service")
        self.assertIsNotNone(spec, "app.search.image_service must exist")
        if spec is None:
            return
        image_service = importlib.import_module("app.search.image_service")
        result = {
            "title": "primary",
            "image_url": "https://img.example.org/primary.jpg",
            "thumbnail_url": "",
            "page_url": "https://example.org/primary",
            "source": "searxng",
        }
        searx = AsyncMock(return_value=[result])
        ddgs = AsyncMock(return_value=[])
        settings = Settings(
            _env_file=None,
            searxng_url="http://searxng:8080",
            search_backend="auto",
        )
        with (
            patch.object(image_service, "search_searxng_images", new=searx),
            patch.object(image_service, "search_ddgs_images", new=ddgs),
        ):
            results = await image_service.search_images("x", settings)
        self.assertEqual(results, [result])
        ddgs.assert_not_awaited()


class ImageSearchConfigAndToolTests(unittest.TestCase):
    def test_image_search_uses_unified_search_backend(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.search_backend, "auto")
        self.assertFalse(hasattr(settings, "image_search_backend"))
        self.assertEqual(settings.image_search_max_results, 4)

    def test_orchestrator_exposes_separate_image_search_tool(self) -> None:
        names = [tool["function"]["name"] for tool in orchestrator.TOOLS]
        self.assertIn("web_search", names)
        self.assertIn("image_search", names)

    def test_answer_carries_image_results(self) -> None:
        answer = orchestrator.Answer(text="ok", provider="fake")
        self.assertTrue(hasattr(answer, "images"), "Answer.images must exist")
        if hasattr(answer, "images"):
            self.assertEqual(answer.images, [])


class TelegramImageDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_falls_back_to_thumbnail_and_skips_broken_images(self) -> None:
        spec = importlib.util.find_spec("app.bot.image_results")
        self.assertIsNotNone(spec, "app.bot.image_results must exist")
        if spec is None:
            return
        image_results = importlib.import_module("app.bot.image_results")

        bot = SimpleNamespace(send_photo=AsyncMock())
        bot.send_photo.side_effect = [
            RuntimeError("full image rejected"),
            None,
            RuntimeError("second full rejected"),
        ]
        images = [
            {
                "title": "one",
                "image_url": "https://img.example.org/one.jpg",
                "thumbnail_url": "https://img.example.org/one-thumb.jpg",
                "page_url": "https://example.org/one",
                "source": "x",
            },
            {
                "title": "two",
                "image_url": "https://img.example.org/two.jpg",
                "thumbnail_url": "",
                "page_url": "https://example.org/two",
                "source": "x",
            },
        ]
        sent = await image_results.send_image_results(
            bot,
            -100123,
            images,
            message_thread_id=99,
        )
        self.assertEqual(sent, 1)
        self.assertEqual(bot.send_photo.await_count, 3)
        self.assertEqual(
            bot.send_photo.await_args_list[1].kwargs["photo"],
            "https://img.example.org/one-thumb.jpg",
        )
        self.assertEqual(bot.send_photo.await_args_list[1].kwargs["message_thread_id"], 99)


if __name__ == "__main__":
    unittest.main()
