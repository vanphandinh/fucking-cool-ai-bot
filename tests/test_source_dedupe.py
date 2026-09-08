"""Regression tests for reference-source canonicalization and diversity."""

import unittest

from app.core.formatting import format_sources
from app.core.orchestrator import _dedupe_sources


_STATUS_URL = "https://x.com/0x0SojalSec/status/2097087104534888698"
_VIDEO_URL = _STATUS_URL + "/video/1"

_RAWDATA_SOURCES = [
    {
        "title": 'Md Ismail Šojal 🕷️ on X: "The most expensive “hello” I\'ve ever ...',
        "url": _STATUS_URL,
        "snippet": "",
    },
    {
        "title": 'Md Ismail Šojal 🕷️ on X: "The most expensive “hello” I\'ve ever ...',
        "url": _VIDEO_URL,
        "snippet": "",
    },
    {
        "title": "0xSojalSec (MD ISMAIL SOJAL) · GitHub",
        "url": "https://github.com/0xSojalSec",
        "snippet": "",
    },
    {
        "title": "0xSojalSec’s gists",
        "url": "https://gist.github.com/0xSojalSec",
        "snippet": "",
    },
    {
        "title": '"Hello" - 주식 - 에펨코리아',
        "url": "https://www.fmkorea.com/10310466183",
        "snippet": "",
    },
    {
        "title": "Md Ismail Šojal (@0x0SojalSec) / Posts / X",
        "url": "https://x.com/0x0SojalSec",
        "snippet": "",
    },
]


class SourceDedupeTests(unittest.TestCase):
    def test_x_status_media_variant_collapses_to_canonical_status(self):
        sources = _dedupe_sources(
            [
                {"title": "video", "url": _VIDEO_URL, "snippet": ""},
                {"title": "status", "url": _STATUS_URL, "snippet": ""},
            ]
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["url"], _STATUS_URL)

    def test_reference_list_keeps_one_entry_per_source_family(self):
        sources = _dedupe_sources(_RAWDATA_SOURCES)

        self.assertEqual(
            [item["url"] for item in sources],
            [
                _STATUS_URL,
                "https://github.com/0xSojalSec",
                "https://www.fmkorea.com/10310466183",
            ],
        )

    def test_format_sources_defensively_applies_same_diversity_rules(self):
        footer = format_sources(_RAWDATA_SOURCES)

        self.assertEqual(footer.count('<a href="'), 3)
        self.assertNotIn("/video/1", footer)
        self.assertNotIn("gist.github.com", footer)
        self.assertNotIn('href="https://x.com/0x0SojalSec"', footer)
        self.assertIn(_STATUS_URL, footer)
        self.assertIn("https://github.com/0xSojalSec", footer)
        self.assertIn("https://www.fmkorea.com/10310466183", footer)


if __name__ == "__main__":
    unittest.main()
