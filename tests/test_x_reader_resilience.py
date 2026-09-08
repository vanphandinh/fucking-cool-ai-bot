from __future__ import annotations

import unittest

import httpx

from app.search.x_reader import XStatusTarget, read_x_url


class XReaderMalformedUpstreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_fxtwitter_code_still_falls_back_to_oembed(self):
        calls: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.host)
            if request.url.host == "api.fxtwitter.com":
                return httpx.Response(
                    200,
                    json={"code": {}, "message": "malformed upstream payload"},
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "author_name": "Alice",
                    "html": '<blockquote><p>official fallback works</p></blockquote>',
                },
                request=request,
            )

        target = XStatusTarget("1234567890", "https://x.com/alice/status/1234567890")
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await read_x_url(target, "auto", timeout=1.0, client=client)

        self.assertEqual(calls, ["api.fxtwitter.com", "publish.x.com"])
        self.assertIsNotNone(result)
        self.assertEqual(result.backend, "x_oembed")
        self.assertIn("official fallback works", result.text)


if __name__ == "__main__":
    unittest.main()
