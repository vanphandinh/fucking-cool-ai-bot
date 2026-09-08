from __future__ import annotations

import unittest

import httpx

from app.search.x_reader import XFetchError, XStatusTarget, _fetch_fxtwitter, parse_x_status_url, read_x_url


class XStatusUrlTests(unittest.TestCase):
    def test_supported_status_urls_are_canonicalized(self):
        cases = {
            "https://x.com/TrustlessState/status/2097054140350554620": (
                "2097054140350554620",
                "https://x.com/TrustlessState/status/2097054140350554620",
            ),
            "https://twitter.com/user/status/1234567890?ref=abc": (
                "1234567890",
                "https://x.com/user/status/1234567890",
            ),
            "https://mobile.twitter.com/user/status/1234567890/photo/1": (
                "1234567890",
                "https://x.com/user/status/1234567890",
            ),
            "https://fxtwitter.com/user/status/1234567890": (
                "1234567890",
                "https://x.com/user/status/1234567890",
            ),
            "https://fixupx.com/user/status/1234567890": (
                "1234567890",
                "https://x.com/user/status/1234567890",
            ),
            "https://x.com/i/status/1234567890": (
                "1234567890",
                "https://x.com/i/status/1234567890",
            ),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                target = parse_x_status_url(url)
                self.assertIsNotNone(target)
                self.assertEqual((target.status_id, target.canonical_url), expected)

    def test_non_status_or_deceptive_hosts_are_not_specialized(self):
        for url in (
            "https://x.com/TrustlessState",
            "https://x.com.evil.example/user/status/1234567890",
            "https://example.org/user/status/1234567890",
            "https://x.com/user/status/not-a-number",
            "https://x.com/user/status/1",
            "https://x.com/user/status/123456789012345678901",
        ):
            with self.subTest(url=url):
                self.assertIsNone(parse_x_status_url(url))


class FxTwitterClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_endpoint_returns_compact_structured_text(self):
        seen = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "status": {
                        "id": "1234567890",
                        "text": "Frame transactions mean nobody needs ETH",
                        "created_at": "2026-09-08T00:00:00Z",
                        "likes": 12,
                        "reposts": 3,
                        "quotes": 2,
                        "replies": 4,
                        "views": 500,
                        "author": {"name": "Alice", "screen_name": "alice"},
                        "media": {"all": [{"type": "photo", "url": "https://cdn.example/raw-photo.jpg", "altText": "chart"}]},
                    },
                },
                request=request,
            )

        target = XStatusTarget("1234567890", "https://x.com/alice/status/1234567890")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await _fetch_fxtwitter(target, "auto", client)

        self.assertEqual(seen, ["https://api.fxtwitter.com/2/status/1234567890"])
        self.assertEqual(result.backend, "fxtwitter_status")
        self.assertFalse(result.thread_complete)
        self.assertIn("Alice (@alice)", result.text)
        self.assertIn("Frame transactions", result.text)
        self.assertIn("photo", result.text)
        self.assertIn("chart", result.text)
        self.assertNotIn("raw-photo.jpg", result.text)

    async def test_thread_endpoint_dedupes_focal_and_limits_posts(self):
        focal = {"id": "1234567890", "text": "root", "author": {"name": "Alice", "screen_name": "alice"}}
        replies = [
            {"id": str(1234567891 + i), "text": f"reply {i}", "author": {"name": "Alice", "screen_name": "alice"}}
            for i in range(20)
        ]

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 200, "status": focal, "thread": [focal, *replies]}, request=request)

        target = XStatusTarget("1234567890", "https://x.com/alice/status/1234567890")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await _fetch_fxtwitter(target, "x_thread", client)

        self.assertEqual(result.backend, "fxtwitter_thread")
        self.assertTrue(result.thread_complete)
        self.assertEqual(result.text.count("root"), 1)
        self.assertIn("Thread truncated", result.text)
        self.assertLessEqual(len(result.text), 5500)

    async def test_json_code_failure_is_an_error_even_on_http_200(self):
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": 404, "message": "not found"}, request=request)

        target = XStatusTarget("1234567890", "https://x.com/a/status/1234567890")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(XFetchError):
                await _fetch_fxtwitter(target, "auto", client)


class XFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_falls_back_to_official_oembed(self):
        calls = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.host)
            if request.url.host == "api.fxtwitter.com":
                return httpx.Response(503, request=request)
            return httpx.Response(
                200,
                json={
                    "author_name": "Alice",
                    "author_url": "https://x.com/alice",
                    "html": '<blockquote class="twitter-tweet"><p lang="en">Hello &amp; goodbye <a href="https://t.co/x">link</a></p>&mdash; Alice (@alice)</blockquote>',
                },
                request=request,
            )

        target = XStatusTarget("1234567890", "https://x.com/alice/status/1234567890")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await read_x_url(target, "auto", timeout=1.0, client=client)

        self.assertEqual(calls, ["api.fxtwitter.com", "publish.x.com"])
        self.assertEqual(result.backend, "x_oembed")
        self.assertIn("Hello & goodbye link", result.text)
        self.assertIn("Alice", result.text)

    async def test_thread_degrades_to_focal_status_before_oembed(self):
        paths = []

        def respond(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path.startswith("/2/thread/"):
                return httpx.Response(503, request=request)
            if request.url.path.startswith("/2/status/"):
                return httpx.Response(
                    200,
                    json={"code": 200, "status": {"id": "1234567890", "text": "focal only", "author": {"name": "Alice", "screen_name": "alice"}}},
                    request=request,
                )
            raise AssertionError("oEmbed should not be reached")

        target = XStatusTarget("1234567890", "https://x.com/alice/status/1234567890")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await read_x_url(target, "x_thread", timeout=1.0, client=client)

        self.assertEqual(paths, ["/2/thread/1234567890", "/2/status/1234567890"])
        self.assertFalse(result.thread_complete)
        self.assertIn("Full thread unavailable", result.text)
        self.assertIn("focal only", result.text)

    async def test_all_specialized_failures_return_none(self):
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, request=request)

        target = XStatusTarget("1234567890", "https://x.com/alice/status/1234567890")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await read_x_url(target, "auto", timeout=1.0, client=client)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
