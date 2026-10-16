"""PR3 regression checks for lexical source ranking and independence gating.

The V2 web policy must rank by relevance, dedupe canonical URLs, interleave
domains, and only accept a backend early when two independent domains carry a
relevant result. Image search, the X resolver and the flag-off V1 path keep
their previous behaviour.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.search import image_service, router, service, source_ranking
from app.search.runtime import get_search_runtime


def _hit(url: str, title: str, snippet: str = "", **extra) -> dict:
    return {"title": title, "url": url, "snippet": snippet, **extra}


QUERY = "solar capacity 2026"


class LexicalSourceScoreTests(unittest.TestCase):
    def test_title_weight_is_double_the_snippet_weight(self):
        title_only = source_ranking.lexical_score(
            QUERY, _hit("https://a.example.com/1", QUERY)
        )
        snippet_only = source_ranking.lexical_score(
            QUERY, _hit("https://a.example.com/2", "", QUERY)
        )
        self.assertAlmostEqual(title_only, 2 / 3, places=6)
        self.assertAlmostEqual(snippet_only, 1 / 3, places=6)

    def test_score_is_accent_and_case_insensitive_for_vietnamese(self):
        accented = source_ranking.lexical_score(
            "Sản lượng điện mặt trời", _hit("https://a.example.com", "san luong dien mat troi")
        )
        plain = source_ranking.lexical_score(
            "san luong dien mat troi", _hit("https://a.example.com", "Sản lượng điện mặt trời")
        )
        self.assertEqual(accented, plain)
        self.assertGreater(accented, 0)

    def test_empty_query_and_unrelated_results_score_zero(self):
        self.assertEqual(
            source_ranking.lexical_score("", _hit("https://a.example.com", "x")), 0.0
        )
        self.assertEqual(
            source_ranking.lexical_score(
                "quantum", _hit("https://a.example.com", "gardening tips")
            ),
            0.0,
        )


class RankWebResultsTests(unittest.TestCase):
    def test_canonical_duplicates_keep_the_strongest_snippet_for_ranking(self):
        """A weak first backend excerpt must not suppress a stronger duplicate."""
        query = "water safety advisory"
        ranked = source_ranking.rank_web_results(query, [
            _hit("https://civic.example.org/notice?utm_source=search", "Home", "Menu"),
            _hit("https://civic.example.org/notice", query, "water safety advisory update"),
            _hit("https://other.example.net/report", "water safety advisory", ""),
        ])
        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0]["url"], "https://civic.example.org/notice")
        self.assertEqual(ranked[0]["title"], query)

    def test_verification_keeps_a_relevant_recent_alternative_in_full_result_pool(self):
        """A recently changed policy should remain discoverable behind eight old hits."""
        cases = (
            ("verify workplace evacuation policy update", "workplace evacuation policy"),
            ("xác minh tiêu chuẩn nước uống cập nhật", "tiêu chuẩn nước uống"),
            ("verify software security release date", "software security release"),
        )
        for query, updated_title in cases:
            with self.subTest(query=query):
                older = [
                    _hit(f"https://archive{i}.archive{i}.org/history", query, query)
                    for i in range(8)
                ]
                update = _hit(
                    "https://updates.example.net/revision", updated_title,
                    "new revision notice", published_at=(
                        datetime.now(timezone.utc) - timedelta(days=3)
                    ).isoformat(),
                )
                self.assertGreaterEqual(source_ranking.lexical_score(query, update), 0.3)
                ranked = source_ranking.rank_web_results(
                    query, older + [update], limit=8, verification_context=True,
                )
                self.assertEqual(len(ranked), 8)
                self.assertIn(update["url"], [item["url"] for item in ranked])

    def test_recency_slot_requires_query_intent_and_relevance(self):
        query = "workplace evacuation policy"
        older = [
            _hit(f"https://archive{i}.archive{i}.org/history", query, query)
            for i in range(8)
        ]
        recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        weak = _hit(
            "https://unrelated.example.net/story", "gardening", "flowers", published_at=recent
        )
        related = _hit(
            "https://updates.example.net/revision", "workplace evacuation",
            "new revision", published_at=recent,
        )
        for tail, verify in (([weak], True), ([related], False)):
            with self.subTest(verify=verify):
                ranked = source_ranking.rank_web_results(
                    query, older + tail, limit=8, verification_context=verify,
                )
                self.assertEqual([item["url"] for item in ranked], [item["url"] for item in older])

    def test_newest_relevant_release_is_reserved_when_another_recent_page_is_shown(self):
        """Regression for the Q6 freshness-recall failure.

        The reservation used to be skipped as soon as *any* recent page was
        already displayed, so a fresh, above-floor official release stayed out
        of the top results while an older mid-relevance recent page held the
        slot. The newest eligible candidate must be reserved instead: exactly
        one slot changes hands, and it is the weakest displayed hit that makes
        way even though it outscores the fresh candidate.
        """
        now = datetime.now(timezone.utc)
        query = "theo trang chinh thuc cua NASA ve chuong trinh Artemis lan ha canh"
        archived = [
            _hit(
                f"https://archive{i}.example{i}.org/history",
                query,
                query,
                published_at=(now - timedelta(days=900 + i)).isoformat(),
            )
            for i in range(7)
        ]
        mid = _hit(
            "https://www.space.com/artemis-overview",
            query,
            "",
            published_at=(now - timedelta(days=300)).isoformat(),
        )
        release = _hit(
            "https://www.nasa.gov/news-release/artemis-architecture",
            "chinh thuc NASA Artemis chuong trinh lan ha canh su menh",
            "",
            published_at=(now - timedelta(days=30)).isoformat(),
        )
        ranked = source_ranking.rank_web_results(
            query, archived + [mid, release], limit=8, verification_context=True
        )
        urls = [item["url"] for item in ranked]
        self.assertEqual(len(ranked), 8)
        self.assertIn(release["url"], urls)
        self.assertNotIn(mid["url"], urls)
        for item in archived:
            self.assertIn(item["url"], urls)

    def test_recent_but_unrelated_page_is_never_reserved(self):
        """The reservation may not displace a relevant hit with a fresh filler."""
        now = datetime.now(timezone.utc)
        query = "theo trang chinh thuc cua NASA ve chuong trinh Artemis lan ha canh"
        archived = [
            _hit(
                f"https://archive{i}.example{i}.org/history",
                query,
                query,
                published_at=(now - timedelta(days=900 + i)).isoformat(),
            )
            for i in range(8)
        ]
        off_topic = _hit(
            "https://weather.example.net/today",
            "thoi tiet hom nay",
            "du bao thoi tiet",
            published_at=(now - timedelta(days=1)).isoformat(),
        )
        ranked = source_ranking.rank_web_results(
            query, archived + [off_topic], limit=8, verification_context=True
        )
        self.assertNotIn(off_topic["url"], [item["url"] for item in ranked])

    def test_verification_does_not_trust_government_tld_over_relevance(self):
        """A domain suffix alone says nothing about which claim a page supports."""
        query = "medical treatment guideline evidence"
        weak_gov = _hit("https://catalog.gov/news", "medical treatment", "press archive")
        relevant = _hit(
            "https://medical-society.org/guideline", query, "updated consensus document"
        )
        ranked = source_ranking.rank_web_results(
            query, [weak_gov, relevant], verification_context=True
        )
        self.assertEqual(ranked[0]["url"], relevant["url"])

    def test_verification_intent_is_multilingual_and_domain_neutral(self):
        for text in (
            "Hãy xác minh tin này",
            "Thông tin đó có đúng không?",
            "Fact-check this report",
            "Is this claim true or false?",
            "Theo nguồn cập nhật mới nhất",
        ):
            with self.subTest(text=text):
                self.assertTrue(source_ranking.verification_intent(text))
        self.assertFalse(source_ranking.verification_intent("How do I boil rice?"))

    def test_recent_published_sources_win_ties_only_for_freshness_queries(self):
        now = datetime.now(timezone.utc)
        older = _hit("https://catalog.example.com/old", "Artemis mission", "NASA",
                     published_at=(now - timedelta(days=450)).isoformat())
        recent = _hit("https://catalog.example.com/new", "Artemis mission", "NASA",
                      published_at=(now - timedelta(days=3)).isoformat())
        self.assertEqual(
            source_ranking.rank_web_results(
                "latest Artemis mission NASA", [older, recent],
            )[0]["url"],
            "https://catalog.example.com/new",
        )
        self.assertEqual(
            source_ranking.rank_web_results("Artemis mission NASA", [older, recent])[0]["url"],
            "https://catalog.example.com/old",
        )

    def test_official_source_queries_get_the_recency_hint_too(self):
        now = datetime.now(timezone.utc)
        older = _hit("https://catalog.example.com/old", "Artemis mission", "NASA",
                     published_at=(now - timedelta(days=450)).isoformat())
        recent = _hit("https://catalog.example.com/new", "Artemis mission", "NASA",
                      published_at=(now - timedelta(days=3)).isoformat())
        # "Theo trang chính thức" asks for current official facts even without
        # an explicit newest/updated keyword, so recency may break the tie.
        self.assertEqual(
            source_ranking.rank_web_results(
                "theo trang chinh thuc cua NASA ve chuong trinh Artemis", [older, recent],
            )[0]["url"],
            "https://catalog.example.com/new",
        )

    def test_canonical_dedupe_and_limit(self):
        results = [
            _hit("https://a.example.com/x?utm_source=tool#frag", QUERY),
            _hit("https://A.example.com:443/x/", QUERY),
            _hit("https://b.example.net/y", QUERY),
        ]
        ranked = source_ranking.rank_web_results(QUERY, results, limit=8)
        self.assertEqual(
            [item["url"] for item in ranked],
            ["https://a.example.com/x", "https://b.example.net/y"],
        )
        self.assertEqual(len(source_ranking.rank_web_results(QUERY, results, limit=1)), 1)

    def test_domain_diversity_interleaves_breadth_before_depth(self):
        results = [
            _hit("https://catalog.example.com/0", QUERY),
            _hit("https://catalog.example.com/1", QUERY),
            _hit("https://catalog.example.com/2", QUERY),
            _hit("https://bulletin.example.net/3", QUERY),
        ]
        ranked = source_ranking.rank_web_results(QUERY, results, limit=8)
        self.assertEqual(
            [item["url"] for item in ranked],
            [
                "https://catalog.example.com/0",
                "https://bulletin.example.net/3",
                "https://catalog.example.com/1",
                "https://catalog.example.com/2",
            ],
        )

    def test_more_relevant_result_sorts_first_regardless_of_input_order(self):
        results = [
            _hit("https://catalog.example.com/intro", "Danh mục chung"),
            _hit("https://bulletin.example.net/report", QUERY),
        ]
        ranked = source_ranking.rank_web_results(QUERY, results, limit=8)
        self.assertEqual(ranked[0]["url"], "https://bulletin.example.net/report")

    def test_dates_are_never_used_to_infer_freshness(self):
        dated_old = _hit("https://catalog.example.com/a", QUERY, "", date="2019-01-01")
        dated_new = _hit("https://catalog.example.com/b", QUERY, "", date="2030-01-01")
        ranked = source_ranking.rank_web_results(QUERY, [dated_old, dated_new], limit=8)
        self.assertEqual(
            [item["url"] for item in ranked],
            ["https://catalog.example.com/a", "https://catalog.example.com/b"],
        )

    def test_all_unique_results_survive_and_junk_is_dropped(self):
        results = [_hit(f"https://catalog.example.com/{i}", QUERY) for i in range(9)]
        results.append({"title": "no url", "url": "", "snippet": QUERY})
        results.append("not-a-dict")
        ranked = source_ranking.rank_web_results(QUERY, results, limit=8)
        self.assertEqual(len(ranked), 8)
        self.assertEqual(ranked[0]["url"], "https://catalog.example.com/0")
        self.assertEqual(ranked[1]["url"], "https://catalog.example.com/1")


class IndependenceGateTests(unittest.TestCase):
    def test_two_relevant_domains_are_enough(self):
        results = [
            _hit("https://a.example.com/1", QUERY),
            _hit("https://b.example.net/1", "", QUERY),
        ]
        self.assertTrue(source_ranking.has_independent_coverage(QUERY, results))

    def test_single_domain_repetition_is_not_independent(self):
        results = [
            _hit("https://a.example.com/1", QUERY),
            _hit("https://a.example.com/2", QUERY),
        ]
        self.assertFalse(source_ranking.has_independent_coverage(QUERY, results))

    def test_irrelevant_second_domain_does_not_satisfy_the_gate(self):
        results = [
            _hit("https://a.example.com/1", QUERY),
            _hit("https://b.example.net/1", "", "unrelated gardening notes"),
        ]
        self.assertFalse(source_ranking.has_independent_coverage(QUERY, results))

    def test_threshold_boundary_separates_weak_from_relevant_domains(self):
        partial = _hit("https://b.example.net/1", "solar", "")
        # "solar" is one of three query terms and the title counts double.
        self.assertAlmostEqual(source_ranking.lexical_score(QUERY, partial), 2 / 9, places=6)
        self.assertGreater(source_ranking.lexical_score(QUERY, partial), 0.20)

        weak = _hit("https://b.example.net/1", "solar", "")
        long_query = "solar capacity 2026 annual report summary"
        self.assertLess(source_ranking.lexical_score(long_query, weak), 0.20)
        self.assertFalse(
            source_ranking.has_independent_coverage(
                long_query, [_hit("https://a.example.com/1", long_query), weak]
            )
        )


class MergePoolTests(unittest.TestCase):
    """The V2 candidate pool must keep distinct pages ahead of extra excerpts."""

    def _merge(self, current, incoming, *, limit, keep=True):
        return router._merge_results(
            current, incoming, key_name="url", limit=limit, keep_duplicate_excerpts=keep
        )

    def test_flood_of_one_url_cannot_evict_urls_from_the_next_backend(self):
        flooded = _hit("https://echo.example.org/solar", "Danh mục chung", "trang phụ")
        flood = [{**flooded, "snippet": f"trang phụ {i}"} for i in range(24)]
        later = [_hit(f"https://new{i}.example.net/report", f"báo cáo {i}") for i in range(5)]

        pool = self._merge([], flood, limit=24)
        pool = self._merge(pool, later, limit=24)

        urls = [item["url"] for item in pool]
        self.assertLessEqual(len(urls), 24)
        self.assertIn(flooded["url"], urls)
        for item in later:
            self.assertIn(item["url"], urls, "a page no backend had offered yet was evicted")

    def test_alternate_excerpts_are_kept_while_the_pool_has_room(self):
        url = "https://city.example.org/advisory"
        pool = self._merge(
            [_hit(url, "City homepage", "Welcome")],
            [_hit(url, QUERY, "Revised advisory update")],
            limit=24,
        )
        self.assertEqual([item["url"] for item in pool], [url, url])
        self.assertEqual(pool[1]["title"], QUERY)

    def test_flag_off_keeps_first_hit_wins_without_extra_excerpts(self):
        url = "https://city.example.org/advisory"
        pool = self._merge(
            [_hit(url, "City homepage", "Welcome")],
            [_hit(url, QUERY, "Revised advisory update")],
            limit=24,
            keep=False,
        )
        self.assertEqual([item["url"] for item in pool], [url])
        self.assertEqual(pool[0]["title"], "City homepage")

    def test_pool_never_exceeds_the_limit(self):
        flood = [_hit("https://echo.example.org/x", "t", str(i)) for i in range(50)]
        pool = self._merge([], flood, limit=7)
        self.assertEqual(len(pool), 7)


class RoutedSourceRankingTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self, **overrides) -> Settings:
        return Settings(
            _env_file=None,
            search_backend="auto",
            searxng_url="http://searxng:8080",
            **overrides,
        )

    async def test_v2_cross_backend_duplicate_retains_best_excerpt(self):
        """Routing must give ranking both excerpts for the same canonical URL."""
        query = "verify drinking water advisory update"
        url = "https://city.example.org/advisory"
        searx = AsyncMock(return_value=[
            _hit(url, "City homepage", "Welcome"),
            _hit("https://newspaper.example.net/analysis", query),
        ])
        ddgs = AsyncMock(return_value=[
            _hit(url, query, "Revised drinking water advisory update"),
        ])
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(
                query, self._settings(web_evidence_v2_enabled=True),
            )
        ddgs.assert_awaited_once()
        self.assertEqual(len(results), 2)
        self.assertEqual(next(item for item in results if item["url"] == url)["title"], query)

    async def test_first_merge_call_site_keeps_duplicate_excerpts(self):
        """M5 first call site: the SearXNG shortcut must also feed the pool.

        When SearXNG alone passes the independence gate, ``route_auto`` returns
        early — the merge that produced that pool is the first
        ``keep_duplicate_excerpts`` call site. Dropping alternate excerpts
        there would rank the weaker excerpt for a duplicated URL, so the
        regression asserts the stronger excerpt wins the early-return path.
        """
        query = "solar capacity chart per country"
        duplicated = "https://solar-watch.example.org/report"
        weak = _hit(duplicated, "solar capacity", "solar capacity")
        rich = _hit(
            duplicated,
            "solar capacity report",
            "solar capacity chart per country",
        )
        diverse = _hit(
            "https://grid-news.example.net/data",
            query,
            "solar capacity chart per country data",
        )
        searx = AsyncMock(return_value=[weak, rich, diverse])
        ddgs = AsyncMock(return_value=[])  # must never be called in this path
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(
                query, self._settings(web_evidence_v2_enabled=True)
            )

        ddgs.assert_not_awaited()
        self.assertEqual(len(results), 2)
        kept = next(item for item in results if item["url"] == duplicated)
        # The duplicate passages reached ranking, and the stronger one won.
        self.assertIn("chart per country", str(kept["snippet"]))

    async def test_v2_duplicate_flood_cannot_crowd_out_diverse_sources(self):
        """A backend repeating one page must not starve the merged pool.

        Keeping alternate excerpts is what lets ranking prefer the stronger
        one, but a flood of one canonical URL must never consume the pool that
        an unseen, relevant page from the second backend still needs. This is
        the adversarial counterpart of the cross-backend duplicate case.
        """
        query = "solar capacity 2026"
        flooded = "https://echo.example.org/solar"
        flood = [_hit(flooded, "Danh mục chung", f"trang phụ {i}") for i in range(24)]
        diverse = _hit("https://independent.example.net/report", query, query)
        searx = AsyncMock(return_value=flood)
        ddgs = AsyncMock(return_value=[diverse])
        settings = self._settings(web_evidence_v2_enabled=True)
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(query, settings)

        urls = [item["url"] for item in results]
        self.assertIn(diverse["url"], urls, "the diverse page was dropped by the pool")
        self.assertEqual(urls.count(flooded), 1, "one result per canonical URL")
        self.assertEqual(len(urls), 2)

    async def test_fact_check_tops_up_old_independent_hits_with_recent_source(self):
        """Extra discovery applies to arbitrary claims, independent of source TLD."""
        query = "verify whether treatment protocol Q changed in 2025"
        old = [
            _hit("https://archive.example.com/2024", query),
            _hit("https://history.example.net/2025", query),
        ]
        current_document = _hit(
            "https://medical-society.org/revision",
            "treatment protocol Q revision",
            "new recommendations published",
            published_at=datetime.now(timezone.utc).isoformat(),
        )
        searx = AsyncMock(return_value=old)
        ddgs = AsyncMock(return_value=[current_document])
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(
                query,
                self._settings(web_evidence_v2_enabled=True),
                verification_context=True,
            )
        ddgs.assert_awaited_once()
        self.assertIn(current_document["url"], {item["url"] for item in results})
        self.assertEqual(len(results), 3)

    async def test_v2_top_ups_when_searxng_has_only_one_relevant_domain(self):
        searx = AsyncMock(
            return_value=[
                _hit("https://only.example.com/1", QUERY),
                _hit("https://only.example.com/2", QUERY),
            ]
        )
        ddgs = AsyncMock(return_value=[_hit("https://other.example.net/1", "", QUERY)])
        settings = self._settings(web_evidence_v2_enabled=True)
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(QUERY, settings)
        ddgs.assert_awaited_once()
        self.assertEqual(
            {item["url"] for item in results},
            {
                "https://only.example.com/1",
                "https://only.example.com/2",
                "https://other.example.net/1",
            },
        )

    async def test_v2_accepts_searxng_when_two_independent_domains_are_relevant(self):
        searx = AsyncMock(
            return_value=[
                _hit("https://news.example.com/1", QUERY),
                _hit("https://bulletin.example.net/1", QUERY),
            ]
        )
        ddgs = AsyncMock(return_value=[])
        settings = self._settings(web_evidence_v2_enabled=True)
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(QUERY, settings)
        ddgs.assert_not_awaited()
        self.assertEqual(len(results), 2)

    async def test_v2_rejects_low_quality_searxng_pair_from_one_domain(self):
        searx = AsyncMock(
            return_value=[
                _hit("https://only.example.com/1", "Danh mục chung"),
                _hit("https://only.example.com/2", "Trang giới thiệu"),
            ]
        )
        ddgs = AsyncMock(return_value=[_hit("https://other.example.net/1", QUERY)])
        settings = self._settings(web_evidence_v2_enabled=True)
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(QUERY, settings)
        ddgs.assert_awaited_once()
        self.assertEqual([item["url"] for item in results][0], "https://other.example.net/1")

    async def test_flag_off_keeps_v1_count_threshold_and_order(self):
        searx = AsyncMock(
            return_value=[
                _hit("https://only.example.com/irrelevant", "Danh mục chung"),
                _hit("https://only.example.com/relevant", QUERY),
            ]
        )
        ddgs = AsyncMock(return_value=[])
        settings = self._settings()
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(QUERY, settings)
        ddgs.assert_not_awaited()
        self.assertEqual(
            [item["url"] for item in results],
            ["https://only.example.com/irrelevant", "https://only.example.com/relevant"],
        )

    async def test_v2_reranks_backend_order_by_relevance(self):
        searx = AsyncMock(
            return_value=[
                _hit("https://catalog.example.com/intro", "Danh mục chung"),
                _hit("https://bulletin.example.net/report", QUERY),
                _hit("https://news.example.com/report", QUERY),
            ]
        )
        ddgs = AsyncMock(return_value=[])
        settings = self._settings(web_evidence_v2_enabled=True)
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(QUERY, settings)
        self.assertEqual(results[0]["url"], "https://bulletin.example.net/report")
        self.assertEqual(results[1]["url"], "https://news.example.com/report")
        self.assertEqual(results[2]["url"], "https://catalog.example.com/intro")

    async def test_image_routing_ignores_the_v2_web_gate(self):
        searx = AsyncMock(
            return_value=[
                {"title": "one", "image_url": "https://images.example.com/1"},
                {"title": "two", "image_url": "https://images.example.com/2"},
            ]
        )
        ddgs = AsyncMock(return_value=[])
        settings = self._settings(web_evidence_v2_enabled=True)
        with (
            patch.object(image_service, "search_searxng_images", new=searx),
            patch.object(image_service, "search_ddgs_images", new=ddgs),
        ):
            results = await image_service.search_images("cats", settings)
        ddgs.assert_not_awaited()
        self.assertEqual(len(results), 2)

    async def test_v1_and_v2_cache_entries_never_mix(self):
        searx = AsyncMock(
            return_value=[
                _hit("https://only.example.com/1", QUERY),
                _hit("https://only.example.com/2", QUERY),
            ]
        )
        ddgs = AsyncMock(return_value=[])
        settings = self._settings(web_evidence_v2_enabled=True)
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            await service.search(QUERY, settings)
            await service.search(QUERY, settings)
        # Fresh V2 cache entry: the quality gate already ran once.
        self.assertEqual(searx.await_count, 1)
        self.assertEqual(ddgs.await_count, 1)
        self.assertIn(f"web:8:{QUERY}:v2", get_search_runtime(settings).cache._entries)

        settings.web_evidence_v2_enabled = False
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            rollback = await service.search(QUERY, settings)
        # Rollback must not reuse the V2 ordering or the V2 gate decision.
        self.assertEqual(searx.await_count, 2)
        self.assertEqual(ddgs.await_count, 1)
        self.assertEqual(
            [item["url"] for item in rollback],
            ["https://only.example.com/1", "https://only.example.com/2"],
        )

    async def test_v2_falls_back_to_ddgs_when_searxng_errors(self):
        searx = AsyncMock(side_effect=RuntimeError("searx down"))
        ddgs = AsyncMock(
            return_value=[
                _hit("https://news.example.com/1", QUERY),
                _hit("https://bulletin.example.net/1", QUERY),
            ]
        )
        settings = self._settings(web_evidence_v2_enabled=True)
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search(QUERY, settings)
        ddgs.assert_awaited_once()
        self.assertEqual(len(results), 2)

    async def test_v2_stale_cache_is_used_only_after_both_backends_fail(self):
        cached = [
            _hit("https://news.example.com/1", QUERY),
            _hit("https://bulletin.example.net/1", QUERY),
        ]
        searx = AsyncMock(return_value=cached)
        ddgs = AsyncMock(return_value=[])
        settings = self._settings(
            web_evidence_v2_enabled=True,
            search_web_cache_ttl_sec=0,
            search_stale_cache_ttl_sec=60,
        )
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            first = await service.search(QUERY, settings)
        self.assertEqual([item["url"] for item in first], [item["url"] for item in cached])

        searx.side_effect = RuntimeError("searx down")
        ddgs.side_effect = RuntimeError("ddgs down")
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            second = await service.search(QUERY, settings)
        self.assertEqual([item["url"] for item in second], [item["url"] for item in cached])

    async def test_cancellation_propagates_before_ranking(self):
        started = asyncio.Event()

        async def stalled(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        settings = self._settings(web_evidence_v2_enabled=True)
        with patch("app.search.searxng_backend.search_searxng", new=stalled):
            task = asyncio.create_task(service.search("cancel-me-unique", settings))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    unittest.main()
