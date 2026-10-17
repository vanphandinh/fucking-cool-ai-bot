"""Bounded freshness discovery query for verification questions.

Publication dates are frequently missing from search results (the engines that
supply them can be suspended), so the recency reservation in
``source_ranking`` has nothing to choose from. The orchestrator therefore asks
one extra, current-year discovery query per question — never more than one, and
never past the display cap — when a verification question shows no recent dated
source.
"""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.ai.router import CompletionResult
from app.config import Settings
from app.core.orchestrator import (
    _VN_TZ,
    Orchestrator,
    _has_recent_source,
    _merge_search_results,
)

QUERY = "theo trang chinh thuc cua NASA ve chuong trinh Artemis"
_OLD = {"title": "Artemis archive", "url": "https://archive.example.org/a", "snippet": ""}
_RECENT_DATED = {
    "title": "Artemis update",
    "url": "https://news.example.net/update",
    "snippet": "",
    "published_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
}
_BOOST_ONLY = {
    "title": "Artemis 2026 release",
    "url": "https://www.nasa.gov/news-release/artemis-2026",
    "snippet": "",
}


class _SearchHarness:
    """Calls ``web_search`` the requested number of times, then answers."""

    def __init__(self, calls: int = 1) -> None:
        self.calls = calls
        self.outputs: list[str] = []

    async def complete(self, request, tool_executor, **kwargs):
        for _ in range(self.calls):
            self.outputs.append(await tool_executor("web_search", {"query": QUERY}))
        return CompletionResult("cau tra loi", "fake", ())


class MergeHelpersTests(unittest.TestCase):
    def test_has_recent_source_only_accepts_a_dated_result_inside_the_window(self):
        self.assertFalse(_has_recent_source([_OLD]))
        self.assertTrue(_has_recent_source([_OLD, _RECENT_DATED]))
        stale_at = (datetime.now(timezone.utc) - timedelta(days=900)).isoformat()
        stale = dict(_OLD, published_at=stale_at)
        self.assertFalse(_has_recent_source([stale]))
        self.assertFalse(_has_recent_source(None))

    def test_merge_is_capped_and_dedupes_canonical_urls(self):
        primary = [dict(_OLD, url=f"https://a.example.com/{i}") for i in range(6)]
        extra = [dict(_OLD, url="https://A.example.com/0/"), _BOOST_ONLY]
        merged = _merge_search_results(primary, extra, limit=8)
        self.assertEqual([item["url"] for item in merged][:6], [item["url"] for item in primary])
        self.assertEqual(len(merged), 7)
        self.assertIn(_BOOST_ONLY["url"], [item["url"] for item in merged])

    def test_reserved_slot_admits_a_discovery_result_when_primary_is_full(self):
        primary = [dict(_OLD, url=f"https://a.example.com/{i}") for i in range(8)]
        merged = _merge_search_results(
            primary, [dict(_BOOST_ONLY)], limit=8, reserve_slot=True
        )
        urls = [item["url"] for item in merged]
        self.assertEqual(len(merged), 8)
        self.assertIn(_BOOST_ONLY["url"], urls)
        self.assertNotIn(primary[-1]["url"], urls)

    def test_reserved_slot_survives_extra_repeating_the_dropped_primary_url(self):
        primary = [dict(_OLD, url=f"https://a.example.com/{i}") for i in range(8)]
        extra = [dict(_OLD, url=primary[-1]["url"]), dict(_BOOST_ONLY)]
        merged = _merge_search_results(primary, extra, limit=8, reserve_slot=True)
        urls = [item["url"] for item in merged]
        self.assertEqual(len(merged), 8)
        self.assertIn(_BOOST_ONLY["url"], urls)
        self.assertNotIn(primary[-1]["url"], urls)

    def test_reserved_slot_keeps_primary_when_extra_is_already_present(self):
        primary = [dict(_OLD, url=f"https://a.example.com/{i}") for i in range(8)]
        merged = _merge_search_results(
            primary, [dict(_OLD, url=primary[0]["url"])], limit=8, reserve_slot=True
        )
        self.assertEqual(
            [item["url"] for item in merged], [item["url"] for item in primary]
        )

    def test_zero_limit_returns_no_results(self):
        self.assertEqual(_merge_search_results([_OLD], [_BOOST_ONLY], limit=0), [])


class FreshnessBoostWiringTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self) -> Settings:
        return Settings(
            _env_file=None, search_backend="ddgs", web_evidence_v2_enabled=True
        )

    async def test_boost_runs_once_and_appends_the_current_year(self):
        harness = _SearchHarness(calls=2)
        orchestrator = Orchestrator(self._settings(), harness)
        with patch(
            "app.core.orchestrator.search_service.search",
            new=AsyncMock(side_effect=[[dict(_OLD)], [dict(_BOOST_ONLY)], [dict(_OLD)]]),
        ) as search_mock:
            await orchestrator.ask(QUERY)
        queries = [call.args[0] for call in search_mock.await_args_list]
        year = str(datetime.now(_VN_TZ).year)
        self.assertEqual(queries, [QUERY, f"{QUERY} {year}", QUERY])
        self.assertIn(_BOOST_ONLY["url"], harness.outputs[0])

    async def test_no_boost_when_a_recent_dated_source_is_already_displayed(self):
        harness = _SearchHarness(calls=2)
        orchestrator = Orchestrator(self._settings(), harness)
        with patch(
            "app.core.orchestrator.search_service.search",
            new=AsyncMock(return_value=[dict(_OLD), dict(_RECENT_DATED)]),
        ) as search_mock:
            await orchestrator.ask(QUERY)
        self.assertEqual(search_mock.await_count, 2)

    async def test_no_boost_without_verification_intent(self):
        harness = _SearchHarness(calls=1)
        orchestrator = Orchestrator(self._settings(), harness)
        with patch(
            "app.core.orchestrator.search_service.search",
            new=AsyncMock(return_value=[dict(_OLD)]),
        ) as search_mock:
            await orchestrator.ask("How do I boil rice?")
        self.assertEqual(search_mock.await_count, 1)

    async def test_boost_never_grows_the_listing_past_the_cap(self):
        eight = [dict(_OLD, url=f"https://a{i}.example.com/x") for i in range(8)]
        harness = _SearchHarness(calls=1)
        orchestrator = Orchestrator(self._settings(), harness)
        with patch(
            "app.core.orchestrator.search_service.search",
            new=AsyncMock(side_effect=[eight, [dict(_BOOST_ONLY)]]),
        ):
            await orchestrator.ask(QUERY)
        urls = set(re.findall(r"https?://[^\s<>\"]+", harness.outputs[0]))
        self.assertLessEqual(len(urls), 8)
        self.assertIn(eight[0]["url"], harness.outputs[0])
        self.assertIn(_BOOST_ONLY["url"], urls)
        self.assertNotIn(eight[-1]["url"], urls)


if __name__ == "__main__":
    unittest.main()
