"""PR3 regression checks for lexical source ranking and independence gating.

The V2 web policy must rank by relevance, dedupe canonical URLs, interleave
domains, and only accept a backend early when two independent domains carry a
relevant result. Image search, the X resolver and the flag-off V1 path keep
their previous behaviour.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.search import image_service, service, source_ranking
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


class RoutedSourceRankingTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self, **overrides) -> Settings:
        return Settings(
            _env_file=None,
            search_backend="auto",
            searxng_url="http://searxng:8080",
            **overrides,
        )

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
