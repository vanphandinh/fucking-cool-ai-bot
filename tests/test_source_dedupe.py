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
        self.assertEqual(
            source_family_key("https://news.alice.github.io/story"),
            "alice.github.io",
        )

    def test_generic_url_canonicalization_removes_non_content_variants(self):
        self.assertEqual(
            canonicalize_source_url(
                "HTTPS://WWW.Example.COM:443/story/?utm_source=x&b=2&a=1#comments"
            ),
            "https://www.example.com/story?b=2&a=1",
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

    def test_x_different_publishers_remain_independent(self):
        sources = _dedupe_sources(
            [
                {"title": "Alice", "url": "https://x.com/alice/status/111", "snippet": ""},
                {"title": "Bob", "url": "https://x.com/bob/status/222", "snippet": ""},
            ]
        )
        self.assertEqual(
            [item["url"] for item in sources],
            ["https://x.com/alice/status/111", "https://x.com/bob/status/222"],
        )

    def test_x_same_publisher_collapses_profile_status_and_media_variants(self):
        sources = _dedupe_sources(
            [
                {
                    "title": "Alice post",
                    "url": "https://x.com/Alice/status/111",
                    "snippet": "",
                },
                {
                    "title": "Alice video",
                    "url": "https://x.com/alice/status/111/video/1",
                    "snippet": "",
                },
                {
                    "title": "Alice profile",
                    "url": "https://x.com/alice",
                    "snippet": "",
                },
            ]
        )
        self.assertEqual(len(sources), 1)

    def test_x_platform_cap_keeps_two_publishers_then_preserves_other_sites(self):
        sources = _dedupe_sources(
            [
                {"title": "Alice", "url": "https://x.com/alice/status/1", "snippet": ""},
                {"title": "Bob", "url": "https://x.com/bob/status/2", "snippet": ""},
                {"title": "Carol", "url": "https://x.com/carol/status/3", "snippet": ""},
                {"title": "News", "url": "https://example.org/news", "snippet": ""},
            ]
        )
        self.assertEqual(
            [item["url"] for item in sources],
            [
                "https://x.com/alice/status/1",
                "https://x.com/bob/status/2",
                "https://example.org/news",
            ],
        )

    def test_facebook_different_pages_and_ids_remain_independent(self):
        sources = _dedupe_sources(
            [
                {
                    "title": "Page A",
                    "url": "https://www.facebook.com/pageA/posts/111",
                    "snippet": "",
                },
                {
                    "title": "Page B",
                    "url": "https://m.facebook.com/pageB/posts/222",
                    "snippet": "",
                },
            ]
        )
        self.assertEqual(len(sources), 2)

    def test_facebook_same_numeric_publisher_collapses_story_variants(self):
        sources = _dedupe_sources(
            [
                {
                    "title": "Story",
                    "url": "https://facebook.com/story.php?story_fbid=10&id=123",
                    "snippet": "",
                },
                {
                    "title": "Profile",
                    "url": "https://facebook.com/profile.php?id=123",
                    "snippet": "",
                },
            ]
        )
        self.assertEqual(len(sources), 1)

    def test_github_same_owner_collapses_but_different_owner_survives(self):
        sources = _dedupe_sources(
            [
                {
                    "title": "Alice repo",
                    "url": "https://github.com/alice/project",
                    "snippet": "",
                },
                {
                    "title": "Alice gist",
                    "url": "https://gist.github.com/alice/abc",
                    "snippet": "",
                },
                {
                    "title": "Bob repo",
                    "url": "https://github.com/bob/project",
                    "snippet": "",
                },
            ]
        )
        self.assertEqual(
            [item["url"] for item in sources],
            ["https://github.com/alice/project", "https://github.com/bob/project"],
        )

    def test_unknown_social_publisher_shape_does_not_merge_unrelated_urls(self):
        sources = _dedupe_sources(
            [
                {
                    "title": "Unknown 1",
                    "url": "https://facebook.com/share/p/AAA",
                    "snippet": "",
                },
                {
                    "title": "Unknown 2",
                    "url": "https://facebook.com/share/p/BBB",
                    "snippet": "",
                },
            ]
        )
        self.assertEqual(len(sources), 2)

    def test_supplied_rawdata_case_still_collapses_without_site_specific_rules(self):
        sources = _dedupe_sources(_RAWDATA_SOURCES)

        self.assertEqual(len(sources), 3)
        self.assertEqual(
            [source_family_key(item["url"]) for item in sources],
            ["x.com", "github.com", "fmkorea.com"],
        )


if __name__ == "__main__":
    unittest.main()
