"""Reader failure messages must not become successful source evidence."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.search.url_service import read_url


class EvidenceStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_unverified_requested_section_is_not_successful_evidence(self):
        failure = "Không xác minh được mục được hỏi trong nội dung nguồn đã tải."
        with patch("app.search.url_service.reader.read_page", new=AsyncMock(
            return_value=failure
        )):
            result = await read_url(
                "https://example.org/spec", Settings(_env_file=None, web_evidence_v2_enabled=True),
                query="section 15.5.5",
            )
        self.assertFalse(result.ok)
        self.assertEqual(result.text, failure)
        self.assertEqual(result.backend, "generic_reader")

    async def test_legacy_parser_rejects_large_incomplete_comment(self):
        from app.search import reader

        with self.assertRaises(reader._FetchError):
            await reader._extract_html(b"<!--" + b"x" * 70000)


if __name__ == "__main__":
    unittest.main()
