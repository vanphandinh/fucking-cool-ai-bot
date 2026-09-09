"""Regression tests for resilient SearXNG -> DDGS search routing."""

from __future__ import annotations

import asyncio
import importlib.util
import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.search import image_service, service


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SearchResilienceConfigTests(unittest.TestCase):
    def test_resilient_search_modules_exist(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.search.resilience"))
        self.assertIsNotNone(importlib.util.find_spec("app.search.router"))
        self.assertIsNotNone(importlib.util.find_spec("app.search.runtime"))

    def test_search_timeout_and_resilience_defaults_are_explicit(self) -> None:
        settings = Settings(_env_file=None)
        expected = {
            "searxng_timeout_sec": 7.0,
            "ddgs_timeout_sec": 8.0,
            "search_total_timeout_sec": 15.0,
            "search_circuit_failure_threshold": 3,
            "search_circuit_cooldown_sec": 30.0,
            "search_rate_limit_cooldown_sec": 180.0,
            "search_cache_max_entries": 256,
            "search_web_cache_ttl_sec": 60.0,
            "search_image_cache_ttl_sec": 120.0,
            "search_stale_cache_ttl_sec": 900.0,
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertTrue(hasattr(settings, name), f"missing Settings.{name}")
                self.assertEqual(getattr(settings, name), value)

    def test_backend_timeout_cannot_exceed_total_search_budget(self) -> None:
        with self.assertRaises(ValueError):
            Settings(
                _env_file=None,
                searxng_timeout_sec=20,
                search_total_timeout_sec=15,
            )


class CircuitBreakerTests(unittest.TestCase):
    def _types(self):
        self.assertIsNotNone(importlib.util.find_spec("app.search.resilience"))
        from app.search.resilience import BackendKey, CircuitBreaker, CircuitState, FailureKind

        return BackendKey, CircuitBreaker, CircuitState, FailureKind

    def test_repeated_failures_open_then_half_open_probe_recovers(self) -> None:
        BackendKey, CircuitBreaker, CircuitState, FailureKind = self._types()
        clock = _FakeClock()
        breaker = CircuitBreaker(
            failure_threshold=2,
            cooldown_sec=30,
            rate_limit_cooldown_sec=180,
            clock=clock,
        )
        key = BackendKey(namespace=1, backend="searxng", kind="web")

        self.assertTrue(breaker.allow_request(key))
        breaker.record_failure(key, FailureKind.TIMEOUT, "one")
        self.assertEqual(breaker.state(key), CircuitState.CLOSED)

        breaker.record_failure(key, FailureKind.TIMEOUT, "two")
        self.assertEqual(breaker.state(key), CircuitState.OPEN)
        self.assertFalse(breaker.allow_request(key))

        clock.advance(30)
        self.assertTrue(breaker.allow_request(key))
        self.assertEqual(breaker.state(key), CircuitState.HALF_OPEN)
        self.assertFalse(breaker.allow_request(key), "only one half-open probe is allowed")

        breaker.record_success(key)
        self.assertEqual(breaker.state(key), CircuitState.CLOSED)
        self.assertTrue(breaker.allow_request(key))

    def test_rate_limit_opens_immediately_with_longer_cooldown(self) -> None:
        BackendKey, CircuitBreaker, CircuitState, FailureKind = self._types()
        clock = _FakeClock()
        breaker = CircuitBreaker(
            failure_threshold=3,
            cooldown_sec=30,
            rate_limit_cooldown_sec=180,
            clock=clock,
        )
        key = BackendKey(namespace=1, backend="ddgs", kind="web")
        breaker.record_failure(key, FailureKind.RATE_LIMIT, "429")
        self.assertEqual(breaker.state(key), CircuitState.OPEN)
        clock.advance(179)
        self.assertFalse(breaker.allow_request(key))
        clock.advance(1)
        self.assertTrue(breaker.allow_request(key))


class SearchCacheAndSingleFlightTests(unittest.IsolatedAsyncioTestCase):
    def _types(self):
        self.assertIsNotNone(importlib.util.find_spec("app.search.resilience"))
        from app.search.resilience import SearchCache, SingleFlight

        return SearchCache, SingleFlight

    async def test_cache_has_fresh_then_stale_windows(self) -> None:
        SearchCache, _ = self._types()
        clock = _FakeClock()
        cache = SearchCache(max_entries=2, clock=clock)
        value = [{"url": "https://example.com"}]
        cache.set("k", value, fresh_ttl_sec=10, stale_ttl_sec=30)
        self.assertEqual(cache.get_fresh("k"), value)
        clock.advance(11)
        self.assertIsNone(cache.get_fresh("k"))
        self.assertEqual(cache.get_stale("k"), value)
        clock.advance(20)
        self.assertIsNone(cache.get_stale("k"))

    async def test_singleflight_coalesces_concurrent_identical_work(self) -> None:
        _, SingleFlight = self._types()
        singleflight = SingleFlight()
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def factory():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return ["ok"]

        tasks = [asyncio.create_task(singleflight.run("same", factory)) for _ in range(8)]
        await started.wait()
        release.set()
        results = await asyncio.gather(*tasks)
        self.assertEqual(calls, 1)
        self.assertEqual(results, [["ok"]] * 8)


class ResilientTextRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_never_ping_pongs_back_to_searxng_when_both_backends_fail(self) -> None:
        searx = AsyncMock(side_effect=RuntimeError("searx down"))
        ddgs = AsyncMock(side_effect=RuntimeError("ddgs down"))
        settings = Settings(_env_file=None, search_backend="auto", searxng_url="http://searxng")
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            with self.assertRaises(service.SearchError):
                await service.search("both-fail-no-ping-pong", settings)
        self.assertEqual(searx.await_count, 1)
        self.assertEqual(ddgs.await_count, 1)

    async def test_partial_searxng_results_survive_ddgs_failure(self) -> None:
        partial = [{"title": "one", "url": "https://one", "snippet": ""}]
        searx = AsyncMock(return_value=partial)
        ddgs = AsyncMock(side_effect=RuntimeError("ddgs down"))
        settings = Settings(_env_file=None, search_backend="auto", searxng_url="http://searxng")
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search("partial-survives", settings)
        self.assertEqual(results, partial)
        self.assertEqual(searx.await_count, 1)
        self.assertEqual(ddgs.await_count, 1)

    async def test_partial_results_are_topped_up_and_deduplicated(self) -> None:
        searx = AsyncMock(
            return_value=[{"title": "one", "url": "https://same", "snippet": "s"}]
        )
        ddgs = AsyncMock(
            return_value=[
                {"title": "dup", "url": "https://same", "snippet": "d"},
                {"title": "two", "url": "https://two", "snippet": "d"},
            ]
        )
        settings = Settings(_env_file=None, search_backend="auto", searxng_url="http://searxng")
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            results = await service.search("partial-topup", settings, limit=8)
        self.assertEqual([item["url"] for item in results], ["https://same", "https://two"])

    async def test_fresh_cache_avoids_repeating_upstream_search(self) -> None:
        results = [
            {"title": "one", "url": "https://one", "snippet": ""},
            {"title": "two", "url": "https://two", "snippet": ""},
        ]
        searx = AsyncMock(return_value=results)
        ddgs = AsyncMock(return_value=[])
        settings = Settings(_env_file=None, search_backend="auto", searxng_url="http://searxng")
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            first = await service.search("fresh-cache", settings)
            second = await service.search("fresh-cache", settings)
        self.assertEqual(first, second)
        self.assertEqual(searx.await_count, 1)
        ddgs.assert_not_awaited()

    async def test_open_searxng_circuit_is_skipped_on_next_request(self) -> None:
        searx = AsyncMock(side_effect=RuntimeError("searx down"))
        ddgs = AsyncMock(
            return_value=[{"title": "fallback", "url": "https://fallback", "snippet": ""}]
        )
        settings = Settings(
            _env_file=None,
            search_backend="auto",
            searxng_url="http://searxng",
            search_circuit_failure_threshold=1,
        )
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            await service.search("circuit-first", settings)
            await service.search("circuit-second", settings)
        self.assertEqual(searx.await_count, 1)
        self.assertEqual(ddgs.await_count, 2)

    async def test_stale_cache_is_used_only_after_both_backends_fail(self) -> None:
        cached = [
            {"title": "one", "url": "https://one", "snippet": ""},
            {"title": "two", "url": "https://two", "snippet": ""},
        ]
        searx = AsyncMock(return_value=cached)
        ddgs = AsyncMock(return_value=[])
        settings = Settings(
            _env_file=None,
            search_backend="auto",
            searxng_url="http://searxng",
            search_web_cache_ttl_sec=0,
            search_stale_cache_ttl_sec=60,
        )
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            first = await service.search("stale-cache", settings)
        self.assertEqual(first, cached)

        searx.side_effect = RuntimeError("searx down")
        searx.return_value = None
        ddgs.side_effect = RuntimeError("ddgs down")
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            second = await service.search("stale-cache", settings)
        self.assertEqual(second, cached)

    async def test_explicit_searxng_mode_never_calls_ddgs(self) -> None:
        searx = AsyncMock(side_effect=RuntimeError("down"))
        ddgs = AsyncMock(return_value=[])
        settings = Settings(_env_file=None, search_backend="searxng", searxng_url="http://searxng")
        with (
            patch("app.search.searxng_backend.search_searxng", new=searx),
            patch("app.search.ddgs_backend.search_ddgs", new=ddgs),
        ):
            with self.assertRaises(service.SearchError):
                await service.search("strict-searxng", settings)
        ddgs.assert_not_awaited()


class ResilientImageRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_image_auto_does_not_retry_searxng_after_ddgs_failure(self) -> None:
        searx = AsyncMock(side_effect=RuntimeError("searx down"))
        ddgs = AsyncMock(side_effect=RuntimeError("ddgs down"))
        settings = Settings(_env_file=None, search_backend="auto", searxng_url="http://searxng")
        with (
            patch.object(image_service, "search_searxng_images", new=searx),
            patch.object(image_service, "search_ddgs_images", new=ddgs),
        ):
            with self.assertRaises(image_service.ImageSearchError):
                await image_service.search_images("image-both-fail", settings)
        self.assertEqual(searx.await_count, 1)
        self.assertEqual(ddgs.await_count, 1)


if __name__ == "__main__":
    unittest.main()
