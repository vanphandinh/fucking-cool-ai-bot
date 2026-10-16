"""PR3 regression checks for conditional auto-fetch under a question budget.

Auto-fetch must stay off unless both flags are on, read at most the configured
number of sources with bounded concurrency, share one question-scoped budget,
reuse the request-scoped read cache, and report unread sources honestly.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.ai.router import CompletionResult
from app.config import Settings
from app.core.job_control import JobControl
from app.core.job_operations import OperationRunner
from app.core.orchestrator import Orchestrator
from app.core.source_policy import select_diverse_sources
from app.search import auto_fetch
from app.search.url_service import UrlReadResult


def _hit(url: str, title: str, snippet: str = "") -> dict:
    return {"title": title, "url": url, "snippet": snippet}


QUERY = "solar capacity 2026"
SEARCH_RESULTS = [
    _hit("https://catalog.example.com/intro", "Danh mục chung"),
    _hit("https://bulletin.example.net/report", QUERY),
    _hit("https://news.example.com/report", QUERY),
]


class _SearchThenNothing:
    """Calls web_search once and returns without requesting any other tool."""

    def __init__(self) -> None:
        self.output = ""

    async def complete(self, request, tool_executor, **kwargs):
        self.output = await tool_executor("web_search", {"query": QUERY})
        return CompletionResult("answer", "fake", ())


class _SearchTwice:
    def __init__(self) -> None:
        self.outputs: list[str] = []

    async def complete(self, request, tool_executor, **kwargs):
        self.outputs.append(await tool_executor("web_search", {"query": QUERY}))
        self.outputs.append(await tool_executor("web_search", {"query": QUERY}))
        return CompletionResult("answer", "fake", ())


class _SearchThenFetch:
    """Reads the same top source again through fetch_url."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.output = ""

    async def complete(self, request, tool_executor, **kwargs):
        await tool_executor("web_search", {"query": QUERY})
        self.output = await tool_executor("fetch_url", {"url": self.url, "mode": "auto"})
        return CompletionResult("answer", "fake", ())


class _NoToolRouter:
    """Answers directly; the model never asks for the web."""

    async def complete(self, request, tool_executor, **kwargs):
        return CompletionResult("không cần web", "fake", ())


class _ExplicitUrlRouter:
    def __init__(self) -> None:
        self.search_output = ""

    async def complete(self, request, tool_executor, **kwargs):
        await tool_executor("fetch_url", {"url": "https://user.example.org/page"})
        self.search_output = await tool_executor("web_search", {"query": QUERY})
        return CompletionResult("answer", "fake", ())


def _settings(**overrides) -> Settings:
    values = {
        "web_evidence_v2_enabled": True,
        "web_auto_fetch_enabled": True,
        **overrides,
    }
    return Settings(_env_file=None, **values)


class AutoFetchPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_both_flags_are_required(self):
        self.assertTrue(auto_fetch.auto_fetch_active(_settings()))
        self.assertFalse(
            auto_fetch.auto_fetch_active(_settings(web_auto_fetch_enabled=False))
        )
        self.assertFalse(
            auto_fetch.auto_fetch_active(
                _settings(web_evidence_v2_enabled=False, web_auto_fetch_enabled=True)
            )
        )

    async def test_budget_never_goes_negative_and_reports_exhaustion(self):
        budget = auto_fetch.AutoFetchBudget(0.5)
        self.assertFalse(budget.exhausted)
        self.assertEqual(budget.spend(0.2), 0.3)
        self.assertEqual(budget.spend(5), 0.0)
        self.assertTrue(budget.exhausted)
        self.assertEqual(budget.spend(-1), 0.0)

    async def test_targets_skip_weak_already_read_and_snippet_sufficient_results(self):
        results = [
            _hit("https://weak.example.com/x", "gardening"),
            *SEARCH_RESULTS,
        ]
        targets = auto_fetch.select_targets(QUERY, results, limit=2)
        self.assertEqual(
            targets,
            ["https://bulletin.example.net/report", "https://news.example.com/report"],
        )
        self.assertEqual(
            auto_fetch.select_targets(
                QUERY,
                results,
                limit=2,
                already_fetched=["https://bulletin.example.net/report?utm_source=x"],
            )[0],
            "https://news.example.com/report",
        )
        self.assertEqual(auto_fetch.select_targets(QUERY, results, limit=0), [])

    async def test_snippet_that_already_covers_the_query_is_not_fetched(self):
        covered = [_hit("https://news.example.com/report", QUERY, QUERY)]
        self.assertEqual(auto_fetch.select_targets(QUERY, covered, limit=2), [])

    async def test_verification_reads_recent_and_relevant_pages_across_domains(self):
        """Cover a temporal conflict without hardcoded domains, topics or answers."""
        now = datetime.now(timezone.utc)
        for query in (
            "verify treatment protocol Q changed 2025",
            "xác minh chuẩn giao thức ABC 2025",
            "fact check software vulnerability Z 2025",
        ):
            with self.subTest(query=query):
                old = _hit("https://archive.example.com/2024", query, query)
                recent = _hit(
                    "https://independent.org/update",
                    f"Update: {query.split(' ', 1)[-1]}",
                    "revision evidence 2025",
                )
                recent["published_at"] = (now - timedelta(days=2)).isoformat()
                targets = auto_fetch.select_targets(
                    query, [old, recent], limit=2, verification_context=True
                )
                self.assertEqual(targets, [recent["url"], old["url"]])

    async def test_future_timestamp_does_not_displace_relevant_source(self):
        query = "verify claims from protocol XYZ"
        old = _hit("https://archive.example.com/2024", query, query)
        future = _hit("https://independent.org/update", "protocol XYZ", "revision")
        future["published_at"] = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        targets = auto_fetch.select_targets(
            query, [old, future], limit=2, verification_context=True
        )
        self.assertEqual(targets, [old["url"], future["url"]])

    async def test_pack_evidence_respects_cap_and_reports_unread_sources(self):
        entries = [
            {"url": "https://a.example.com/1", "ok": True, "text": "x" * 400, "reason": ""},
            {"url": "https://b.example.com/2", "ok": False, "text": "", "reason": "quá thời gian"},
        ]
        packed = auto_fetch.pack_evidence(entries, max_chars=1200)
        self.assertLessEqual(len(packed), 1200)
        self.assertIn("https://a.example.com/1", packed)
        self.assertIn("Trạng thái: đã đọc", packed)
        self.assertIn("https://b.example.com/2", packed)
        self.assertIn("KHÔNG đọc được nguồn này (quá thời gian)", packed)
        self.assertEqual(auto_fetch.pack_evidence([], max_chars=1200), "")
        self.assertEqual(auto_fetch.pack_evidence(entries, max_chars=0), "")

    async def test_pack_evidence_counts_sources_that_did_not_fit(self):
        entries = [
            {"url": "https://a.example.com/1", "ok": True, "text": "y" * 900, "reason": ""},
            {"url": "https://b.example.com/2", "ok": True, "text": "z" * 900, "reason": ""},
        ]
        packed = auto_fetch.pack_evidence(entries, max_chars=600)
        self.assertLessEqual(len(packed), 600)
        self.assertIn("https://a.example.com/1", packed)
        self.assertTrue(
            "https://b.example.com/2" in packed or "vượt ngân sách chứng cứ" in packed,
            packed,
        )


class AutoFetchOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_web_tool_call_reads_nothing_even_with_both_flags_on(self):
        reads: list[str] = []
        searches: list[str] = []

        async def fake_read(url, settings, mode="auto", *, operations=None, query=""):
            reads.append(url)
            return UrlReadResult("body", url, "generic_reader", True)

        async def fake_search(*args, **kwargs):
            searches.append(str(args[0]) if args else "")
            return list(SEARCH_RESULTS)

        with (
            patch("app.core.orchestrator.url_service.read_url", new=fake_read),
            patch("app.core.orchestrator.search_service.search", new=fake_search),
        ):
            answer = await Orchestrator(_settings(), _NoToolRouter()).ask(QUERY)
        self.assertEqual((reads, searches), ([], []))
        self.assertFalse(answer.searched)

    async def test_flags_off_never_reads_a_source_during_web_search(self):
        router = _SearchThenNothing()
        reads: list[str] = []

        async def fake_read(url, settings, mode="auto", *, operations=None, query=""):
            reads.append(url)
            return UrlReadResult("body", url, "generic_reader", True)

        # The search backend is pinned instead of left to the live providers:
        # this test is about the flag gate, so it must never depend on whether
        # DDGS/SearXNG happen to answer (or throttle) during a suite run.
        # ``test_feature_flags_default_off`` owns the shipped-defaults claim.
        settings = Settings(
            _env_file=None,
            web_evidence_v2_enabled=False,
            web_auto_fetch_enabled=False,
        )
        with _patch_search() as search_mock, patch(
            "app.core.orchestrator.url_service.read_url", new=fake_read
        ):
            answer = await Orchestrator(settings, router).ask(QUERY)

        self.assertEqual(reads, [])
        self.assertEqual(search_mock.await_count, 1, "the search itself still ran")
        self.assertTrue(answer.searched)
        self.assertIn("https://bulletin.example.net/report", router.output)
        self.assertNotIn("[WEB AUTO-FETCH", router.output)

    async def test_enabled_auto_fetch_reads_top_sources_and_labels_evidence(self):
        router = _SearchThenNothing()
        reads: list[str] = []

        async def fake_read(url, settings, mode="auto", *, operations=None, query=""):
            reads.append(url)
            return UrlReadResult(f"evidence for {url}", url, "generic_reader", True)

        searcher = _patch_search()
        with searcher, patch("app.core.orchestrator.url_service.read_url", new=fake_read):
            answer = await Orchestrator(_settings(), router).ask(QUERY)

        self.assertEqual(
            reads,
            ["https://bulletin.example.net/report", "https://news.example.com/report"],
        )
        self.assertIn("[WEB AUTO-FETCH", router.output)
        self.assertIn("evidence for https://bulletin.example.net/report", router.output)
        self.assertTrue(answer.searched)
        self.assertEqual(len(answer.sources), 2)

    async def test_failed_read_is_reported_and_never_counted_as_a_source(self):
        router = _SearchThenNothing()

        async def fake_read(url, settings, mode="auto", *, operations=None, query=""):
            return UrlReadResult("Không tải được trang: HTTP 503", url, "generic_reader", False)

        with _patch_search(), patch("app.core.orchestrator.url_service.read_url", new=fake_read):
            answer = await Orchestrator(_settings(), router).ask(QUERY)

        self.assertIn("KHÔNG đọc được nguồn này", router.output)
        self.assertNotIn("Nội dung URL", router.output)
        # Only the search hits are cited; a failed read never becomes a source.
        expected = {item["url"] for item in select_diverse_sources(list(SEARCH_RESULTS), limit=8)}
        self.assertEqual({item["url"] for item in answer.sources}, expected)
        self.assertTrue(answer.searched)

    async def test_auto_fetch_and_fetch_url_share_one_request_scoped_read(self):
        url = "https://bulletin.example.net/report"
        router = _SearchThenFetch(url)
        reads: list[str] = []

        async def fake_read(target, settings, mode="auto", *, operations=None, query=""):
            reads.append(target)
            return UrlReadResult("cached body", target, "generic_reader", True)

        with _patch_search(), patch("app.core.orchestrator.url_service.read_url", new=fake_read):
            await Orchestrator(_settings(), router).ask(QUERY)
        # One read per target from the auto-fetch burst; the later fetch_url
        # tool call is served from the request-scoped cache.
        self.assertEqual(
            reads,
            ["https://bulletin.example.net/report", "https://news.example.com/report"],
        )
        self.assertIn("cached body", router.output)

    async def test_user_supplied_url_suppresses_auto_fetch(self):
        router = _ExplicitUrlRouter()
        reads: list[str] = []

        async def fake_read(url, settings, mode="auto", *, operations=None, query=""):
            reads.append(url)
            return UrlReadResult("user page body", url, "generic_reader", True)

        with _patch_search(), patch("app.core.orchestrator.url_service.read_url", new=fake_read):
            await Orchestrator(_settings(), router).ask(
                "tóm tắt https://user.example.org/page giúp mình"
            )
        self.assertEqual(reads, ["https://user.example.org/page"])
        self.assertNotIn("[WEB AUTO-FETCH", router.search_output)

    async def test_budget_stops_later_bursts_within_one_question(self):
        router = _SearchTwice()
        reads: list[str] = []

        async def slow_read(url, settings, mode="auto", *, operations=None, query=""):
            reads.append(url)
            await asyncio.sleep(0.5)
            return UrlReadResult("late body", url, "generic_reader", True)

        settings = _settings(web_auto_fetch_budget_sec=0.2)
        with _patch_search(), patch("app.core.orchestrator.url_service.read_url", new=slow_read):
            await Orchestrator(settings, router).ask(QUERY)

        self.assertEqual(len(reads), 2, "first burst reads both targets once")
        self.assertIn("quá thời gian auto-fetch", router.outputs[0])
        self.assertNotIn("[WEB AUTO-FETCH", router.outputs[1])

    async def test_concurrency_is_bounded_by_max_sources(self):
        router = _SearchThenNothing()
        active = 0
        peak = 0

        async def fake_read(url, settings, mode="auto", *, operations=None, query=""):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.02)
                return UrlReadResult(f"body {url}", url, "generic_reader", True)
            finally:
                active -= 1

        with _patch_search(), patch("app.core.orchestrator.url_service.read_url", new=fake_read):
            await Orchestrator(_settings(), router).ask(QUERY)
        self.assertEqual(peak, 2)

    async def test_cancelling_the_question_cancels_auto_fetch(self):
        router = _SearchThenNothing()
        started = asyncio.Event()

        async def stalled(url, settings, mode="auto", *, operations=None, query=""):
            started.set()
            await asyncio.Event().wait()

        with _patch_search(), patch("app.core.orchestrator.url_service.read_url", new=stalled):
            task = asyncio.create_task(Orchestrator(_settings(), router).ask(QUERY))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_controlled_job_slots_and_budget_cover_auto_fetch(self):
        router = _SearchThenNothing()
        slots = asyncio.Semaphore(2)
        operations = OperationRunner(JobControl(renewal_sec=30), slots, lambda event: None)
        reads: list[str] = []

        async def fake_read(url, settings, mode="auto", *, operations=None, query=""):
            async def work():
                reads.append(url)
                await asyncio.sleep(0.02)
                return UrlReadResult(f"body {url}", url, "generic_reader", True)

            if operations is not None:
                return await operations.run(f"read {url}", work, timeout_sec=1.0)
            return await work()

        with _patch_search(), patch("app.core.orchestrator.url_service.read_url", new=fake_read):
            answer = await Orchestrator(_settings(), router).ask(QUERY, operations=operations)

        self.assertEqual(len(reads), 2)
        self.assertEqual(operations.control.active_operations, 0)
        self.assertTrue(answer.searched)

    async def test_two_concurrent_questions_keep_separate_caches_and_budgets(self):
        reads: list[str] = []

        async def fake_read(url, settings, mode="auto", *, operations=None, query=""):
            reads.append(url)
            return UrlReadResult(f"body {url}", url, "generic_reader", True)

        with _patch_search(), patch("app.core.orchestrator.url_service.read_url", new=fake_read):
            await asyncio.gather(
                Orchestrator(_settings(), _SearchThenNothing()).ask(QUERY),
                Orchestrator(_settings(), _SearchThenNothing()).ask(QUERY),
            )
        self.assertEqual(len(reads), 4, "each question reads its own two sources")
        self.assertEqual(
            {url for url in reads},
            {"https://bulletin.example.net/report", "https://news.example.com/report"},
        )


def _patch_search():
    from unittest.mock import AsyncMock

    return patch(
        "app.core.orchestrator.search_service.search",
        new=AsyncMock(return_value=[dict(item) for item in SEARCH_RESULTS]),
    )


if __name__ == "__main__":
    unittest.main()
