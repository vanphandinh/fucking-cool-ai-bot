"""Offline regression checks for V2 URL evidence and V1 rollback."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.search import evidence, reader
from app.search.crawl4ai_client import Crawl4AIReadResult
from app.search.url_service import read_url
from app.search.x_reader import XReadResult


URL = "https://example.org/articles/evidence"


def long_html(distance: int = 28000) -> bytes:
    return (
        "<html><nav>"
        + "irrelevant menu " * 400
        + "</nav><article><h1>Annual report</h1>"
        + "<p>Unrelated introduction to this research topic. </p>" * (distance // 46)
        + "<h2>Renewable energy output</h2><p>Solar capacity rose to 731 megawatts in 2026.</p>"
        + "<h2>Conclusion</h2><p>Other background details.</p></article></html>"
    ).encode("utf-8")


class EvidenceExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_deep_fact_and_heading_survive_tool_budget(self):
        body = long_html(82000)
        text, truncated = await evidence.extract_html(body, "utf-8", 120000)
        self.assertGreater(text.index("731 megawatts"), 25000)
        self.assertFalse(truncated)
        packed = evidence.select_evidence(text, URL, "Solar capacity megawatts 2026")
        self.assertIn("731 megawatts", packed)
        self.assertIn("Renewable energy output", packed)
        self.assertIn("[SOURCE 1 / CHUNK", packed)
        self.assertIn(URL, packed)
        self.assertLessEqual(len(packed), evidence.MAX_TOOL_EVIDENCE_CHARS)

    async def test_short_malformed_and_navigation_only_pages(self):
        for raw in (b"<article><p>Short article text", b"<h1>Start</h1><div>Useful text</div>"):
            text, _ = await evidence.extract_html(raw, "utf-8", 120000)
            self.assertIn("text", text)
        text, _ = await evidence.extract_html(b"<nav>Only navigation</nav>", "utf-8", 120000)
        self.assertEqual(
            evidence.select_evidence(text, URL, "navigation"), "Không tìm được nội dung bài viết."
        )

    async def test_bounded_candidate_and_invalid_charset(self):
        text, truncated = await evidence.extract_html(
            b"<p>" + b"a " * 10000, "not-an-encoding", 8000
        )
        self.assertTrue(truncated)
        self.assertLessEqual(len(text), 8000)
        self.assertIn("Trạng thái", evidence.select_evidence(text, URL, truncated=truncated))

    async def test_raw_long_comment_is_rejected(self):
        with self.assertRaises(reader._FetchError):
            await evidence.extract_html(b"<!--" + b"x" * 70000, "utf-8", 120000)

    async def test_cancel_during_extraction(self):
        task = asyncio.create_task(
            evidence.extract_html(b"<p>hello world</p>" * 80000, "utf-8", 120000)
        )
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    def test_vietnamese_accent_and_summary_section_coverage(self):
        self.assertEqual(
            evidence.terms("Sản lượng điện mặt trời"), evidence.terms("San luong dien mat troi")
        )
        text = (
            "# First section\n"
            + "First facts. " * 90
            + "\n# Middle section\n"
            + "Middle facts. " * 90
            + "\n# Last section\n"
            + "Last facts. " * 60
        )
        packed = evidence.select_evidence(text, URL, "tóm tắt tài liệu")
        self.assertIn("First facts", packed)
        self.assertIn("Middle facts", packed)
        self.assertIn("Last facts", packed)


class EvidenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_navigation_only_page_does_not_report_success(self):
        async def fake_fetch(_client, url, max_bytes=reader.MAX_BYTES):
            if url.startswith("https://r.jina.ai/"):
                raise reader._FetchError("offline")
            return reader._FetchResult(b"<nav>Only navigation</nav>", "utf-8")

        with patch("app.search.reader._fetch_limited", new=fake_fetch):
            result = await read_url(
                URL, Settings(_env_file=None, web_evidence_v2_enabled=True), query="article"
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.text, "Không tìm được nội dung bài viết.")

    async def test_html_fallback_v2_extracts_buried_and_flag_off_stays_v1(self):
        async def fake_fetch(_client, url, max_bytes=reader.MAX_BYTES):
            if url.startswith("https://r.jina.ai/"):
                raise reader._FetchError("offline")
            return reader._FetchResult(long_html(), "utf-8")

        with patch("app.search.reader._fetch_limited", new=fake_fetch):
            old = await read_url(URL, Settings(_env_file=None), query="Solar capacity megawatts")
            new = await read_url(
                URL,
                Settings(_env_file=None, web_evidence_v2_enabled=True),
                query="Solar capacity megawatts",
            )
        self.assertTrue(old.ok)
        self.assertNotIn("731 megawatts", old.text)
        self.assertTrue(new.ok)
        self.assertIn("731 megawatts", new.text)
        self.assertLessEqual(len(new.text), 5400)

    async def test_jina_and_crawl4ai_use_same_evidence_labels(self):
        async def fake_fetch(_client, url, max_bytes=reader.MAX_BYTES):
            return reader._FetchResult(
                (
                    "# Solar generation\n"
                    + "noise " * 2000
                    + "\nSolar capacity rose to 731 megawatts in 2026."
                ).encode(),
                "utf-8",
            )

        settings = Settings(_env_file=None, web_evidence_v2_enabled=True)
        with patch("app.search.reader._fetch_limited", new=fake_fetch):
            jina = await read_url(URL, settings, query="Solar capacity megawatts")
        self.assertIn("731 megawatts", jina.text)
        self.assertIn("[SOURCE 1 / CHUNK", jina.text)
        crawl_settings = Settings(
            _env_file=None,
            web_evidence_v2_enabled=True,
            crawl4ai_enabled=True,
            crawl4ai_egress_proxy_url="http://crawl4ai-proxy:3128",
            crawl4ai_api_token="secret",
        )
        crawl = AsyncMock(
            return_value=Crawl4AIReadResult("# Solar report\nSolar 731 megawatts", URL, True)
        )
        with patch("app.search.url_service.crawl4ai_client.read_page", new=crawl):
            result = await read_url(URL, crawl_settings, query="Solar megawatts")
        self.assertEqual(result.backend, "crawl4ai")
        self.assertIn("[SOURCE 1 / CHUNK", result.text)

    async def test_x_specialized_and_ssrf_guard_are_unchanged(self):
        settings = Settings(_env_file=None, web_evidence_v2_enabled=True)
        blocked = await read_url("http://127.0.0.1/private", settings)
        self.assertFalse(blocked.ok)
        with patch(
            "app.search.url_service.x_reader.read_x_url",
            new=AsyncMock(
                return_value=XReadResult(
                    "X original content", "https://x.com/a/status/1234567890", "x_api"
                )
            ),
        ):
            result = await read_url("https://x.com/a/status/1234567890", settings)
        self.assertEqual(result.text, "X original content")


if __name__ == "__main__":
    unittest.main()
