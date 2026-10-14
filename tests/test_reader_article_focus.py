"""Article extraction regressions for long Jina Reader responses."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.search import reader
from app.search.url_service import read_url
from app.config import Settings


URL = (
    "https://dantri.com.vn/thoi-su/"
    "ha-noi-nghien-cuu-dieu-chinh-mat-do-cay-hoa-sua-o-khu-dan-cu-20261014101516579.htm"
)
TITLE = "Hà Nội nghiên cứu điều chỉnh mật độ cây hoa sữa ở khu dân cư"
ARTICLE_BODY = "Hà Nội đang xem xét điều chỉnh mật độ trồng cây hoa sữa tại khu dân cư."


class ReaderArticleFocusTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_jina_navigation_does_not_hide_article_body(self):
        jina_text = (
            f"Title: {TITLE}\n\nURL Source: {URL}\n\n"
            "Published Time: 2026-10-14T10:25:00+07:00\n\nMarkdown Content:\n"
            + "* [Chuyên mục và liên kết điều hướng](https://dantri.com.vn/)\n" * 200
            + f"\n# {TITLE}\n\n**(Dân trí)** - {ARTICLE_BODY}\n"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(str(request.url), "https://r.jina.ai/" + URL)
            return httpx.Response(200, text=jina_text)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.search.reader._build_client", return_value=client):
            read_result = await read_url(URL, Settings(_env_file=None, crawl4ai_enabled=False))

        self.assertTrue(read_result.ok)
        self.assertEqual(read_result.backend, "generic_reader")
        self.assertIn(ARTICLE_BODY, read_result.text)
        self.assertIn(TITLE, read_result.text)
        self.assertLessEqual(len(read_result.text), reader.MAX_CHARS)
        self.assertNotIn("Chuyên mục và liên kết điều hướng", read_result.text)

    async def test_unstructured_jina_text_remains_unchanged(self):
        content = "Useful plain content " * 500

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=content))
        ) as client:
            result = await reader.read_page("https://example.org/article", client=client)

        self.assertEqual(result, content[:reader.MAX_CHARS])


if __name__ == "__main__":
    unittest.main()
