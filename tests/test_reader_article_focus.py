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
    async def test_rfc_section_missing_in_jina_is_read_from_origin_html(self):
        jina = "Title: RFC 9110\nMarkdown Content:\n# Introduction\nEarly overview only."
        html = (
            "<html><body>" + ("<section><p>Introductory overview only.</p></section>" * 9000)
            + "<section id='section-15.5.5'><h3>15.5.5 404 Not Found</h3>"
            + "<p>The origin server did not find a current representation "
            "for the target resource.</p></section></body></html>"
        )
        requested = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if request.url.host == "r.jina.ai":
                return httpx.Response(200, text=jina)
            return httpx.Response(200, text=html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            extracted = await reader.read_page(
                "https://www.rfc-editor.org/rfc/rfc9110.html", client=client,
                query="RFC 9110 mục 15.5.5 chính xác là gì?", max_extracted_chars=120000,
            )
        self.assertIn("did not find a current representation", extracted)
        self.assertIn("404 Not Found", extracted)
        self.assertLessEqual(len(extracted), 6000)
        self.assertEqual(len(requested), 2)

    async def test_long_jina_spec_extracts_numbered_section_beyond_prefix(self):
        filler = "# 1 Introduction\n" + ("unrelated overview.\n" * 9000)
        target = "## 15.5.5 404 Not Found\nThe server could not find the target resource.\n"
        jina = "Title: RFC 9110\nMarkdown Content:\n" + filler + target

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.host, "r.jina.ai")
            return httpx.Response(200, text=jina)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            extracted = await reader.read_page(
                "https://www.rfc-editor.org/rfc/rfc9110",
                client=client, query="RFC 9110 section 15.5.5 meaning",
                max_extracted_chars=120000,
            )
        self.assertIn("404 Not Found", extracted)
        self.assertIn("could not find the target resource", extracted)
        self.assertLessEqual(len(extracted), 6000)

    async def test_cached_jina_snapshot_falls_back_to_live_page(self):
        stale = (
            "Title: Test Document\n\nURL Source: https://example.org/article\n\n"
            "Warning: This is a cached snapshot of the original page, "
            "consider retry with caching opt-out.\n\n"
            "Markdown Content:\n## Test Article\nStale content."
        )
        live = (
            "<html><head><title>Example Domain</title></head><body>"
            "<main><h1>Example Domain</h1><p>Live source text confirms the "
            "page is a documentation example, with up-to-date information.</p>"
            "</main></body></html>"
        )
        requested = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested.append(str(request.url))
            if str(request.url).startswith("https://r.jina.ai/"):
                return httpx.Response(200, text=stale)
            self.assertEqual(str(request.url), "https://example.org/article")
            return httpx.Response(200, text=live, headers={"content-type": "text/html"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("app.search.reader._build_client", return_value=client):
            result = await read_url(
                "https://example.org/article",
                Settings(_env_file=None, crawl4ai_enabled=False),
                query="Example Domain",
            )
        self.assertTrue(result.ok, result.text)
        self.assertIn("Example Domain", result.text)
        self.assertNotIn("Test Article", result.text)
        self.assertEqual(
            requested, [
                "https://r.jina.ai/https://example.org/article",
                "https://example.org/article",
            ],
        )

    async def test_cached_jina_snapshot_does_not_replace_unavailable_live_page(self):
        stale = (
            "Title: Test Document\nWarning: This is a cached snapshot "
            "of the original page.\nMarkdown Content:\nStale text"
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "r.jina.ai":
                return httpx.Response(200, text=stale)
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await reader.read_page("https://example.org/article", client=client)
        self.assertTrue(result.startswith("Không tải được trang:"), result)
        self.assertNotIn("Stale text", result)

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
