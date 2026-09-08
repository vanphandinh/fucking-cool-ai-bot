"""Regression tests for generic reference-source canonicalization and diversity."""

import unittest

from app.core.formatting import canonicalize_source_url, format_sources, source_family_key
from app.core.orchestrator import _dedupe_sources


_RAWDATA_SOURCES = [
    {
        "title": 'Md Ismail Šojal 🕷️ on X: "The most expensive “hello” I\'ve ever ...',
        "url": "https://x.com/0x0SojalSec/status/2097087104534888698",
        "snippet": "",
    },
    {
        "title": 'Md Ismail Šojal 🕷️ on X: "The most expensive “hello” I\'ve ever ...',
        "url": "https://x.com/0x0SojalSec/status/2097087104534888698/video/1",
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
    def test_source_family_uses_registrable_domain_for_arbitrary_subdomains(self):
        self.assertEqual(
            source_family_key("https://news.shop.example.co.uk/story"),
            "example.co.uk",
        )
        self.assertEqual(
            source_family_key("https://m.example.co.uk/another"),
            "example.co.uk",
        )
        self.assertEqual(
            source_family_key("https://cdn.example.com.au/file"),
            "example.com.au",
        )

    def test_generic_url_canonicalization_removes_non_content_variants(self):
        self.assertEqual(
            canonicalize_source_url(
                "HTTPS://WWW.Example.COM:443/story/?utm_source=x&b=2&a=1#comments"
            ),
            "https://www.example.com/story?a=1&b=2",
        )
        self.assertEqual(
            canonicalize_source_url("http://example.com:80/story/?fbclid=abc"),
            "http://example.com/story",
        )

    def test_reference_list_keeps_one_entry_per_generic_source_family(self):
        sources = _dedupe_sources(
            [
                {
                    "title": "Story A",
                    "url": "https://news.example.co.uk/story-a",
                    "snippet": "",
                },
                {
                    "title": "Story B",
                    "url": "https://m.example.co.uk/story-b",
                    "snippet": "",
                },
                {
                    "title": "Independent",
                    "url": "https://other.example.net/story",
                    "snippet": "",
                },
            ]
        )

        self.assertEqual(
            [item["url"] for item in sources],
            ["https://news.example.co.uk/story-a", "https://other.example.net/story"],
        )

    def test_different_domains_remain_independent_even_for_same_identity(self):
        sources = _dedupe_sources(
            [
                {
                    "title": "Alice Developer",
                    "url": "https://social.example/alice",
                    "snippet": "",
                },
                {
                    "title": "Alice Developer",
                    "url": "https://code.example/alice",
                    "snippet": "",
                },
            ]
        )

        self.assertEqual(len(sources), 2)

    def test_format_sources_defensively_applies_generic_domain_diversity(self):
        footer = format_sources(
            [
                {"title": "One", "url": "https://blog.example.com/a"},
                {"title": "Two", "url": "https://www.example.com/b"},
                {"title": "Three", "url": "https://example.org/c"},
            ]
        )

        self.assertEqual(footer.count('<a href="'), 2)
        self.assertIn("https://blog.example.com/a", footer)
        self.assertNotIn("https://www.example.com/b", footer)
        self.assertIn("https://example.org/c", footer)

    def test_supplied_rawdata_case_still_collapses_without_site_specific_rules(self):
        sources = _dedupe_sources(_RAWDATA_SOURCES)

        self.assertEqual(len(sources), 3)
        self.assertEqual(
            [source_family_key(item["url"]) for item in sources],
            ["x.com", "github.com", "fmkorea.com"],
        )


if __name__ == "__main__":
    unittest.main()
