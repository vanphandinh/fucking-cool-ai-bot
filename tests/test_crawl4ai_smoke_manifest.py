from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from scripts.crawl4ai_varied_fixture import ROUTES
from scripts.smoke_crawl4ai import _crawl, load_urls


class Crawl4AISmokeManifestTests(unittest.TestCase):
    def test_controlled_fixture_has_twenty_distinct_paths(self):
        self.assertEqual(len(ROUTES), 20)
        self.assertEqual(len(set(ROUTES)), 20)

    def test_refuses_private_duplicate_and_insufficient_urls(self):
        valid_urls = [f"http://11.23.0.51{path}" for path in ROUTES]
        for urls in (valid_urls[:-1], valid_urls[:-1] + [valid_urls[0]],
                     valid_urls[:-1] + ["http://127.0.0.1/"]):
            with self.subTest(urls=urls[-1]):
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "urls.txt"
                    path.write_text("\n".join(urls), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_urls(path)

    def test_accepts_exactly_twenty_unique_public_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "urls.txt"
            urls = [f"http://11.23.0.51{route}" for route in ROUTES]
            path.write_text("# disposable test fixture\n" + "\n".join(urls), encoding="utf-8")
            self.assertEqual(load_urls(path), urls)


class Crawl4AISmokeResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_successful_response_with_public_destination_and_markdown(self):
        async def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/crawl")
            self.assertEqual(request.headers["Authorization"], "Bearer example-test-token")
            return httpx.Response(200, json={
                "success": True,
                "results": [{"success": True, "url": "https://example.org/",
                             "markdown": {"raw_markdown": "fixture text"}}],
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            elapsed = await _crawl(client, "http://crawl4ai:11235", "example-test-token",
                                   "https://example.org/", "http://crawl4ai-proxy:3128")
        self.assertGreaterEqual(elapsed, 0)

    async def test_rejects_failures_private_redirect_and_empty_markdown(self):
        scenarios = (
            {"success": False, "results": []},
            {"success": True, "results": [{"success": False, "markdown": "text"}]},
            {"success": True, "results": [{"success": True, "markdown": "text",
                                             "redirected_url": "http://127.0.0.1/private"}]},
            {"success": True, "results": [{"success": True, "markdown": ""}]},
        )
        for body in scenarios:
            with self.subTest(body=body):
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
                ) as client:
                    with self.assertRaises(RuntimeError):
                        await _crawl(client, "http://crawl4ai:11235", "example-test-token",
                                     "https://example.org/", "http://crawl4ai-proxy:3128")
