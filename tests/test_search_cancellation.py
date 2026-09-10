"""Cancellation and lifecycle regressions for resilient search."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.search import router
from app.search.resilience import BackendKey, FailureKind, SingleFlight
from app.search.runtime import (
    close_search_runtimes,
    get_search_runtime,
    reset_search_runtimes,
)


class SingleFlightCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_last_cancelled_waiter_cancels_upstream_task(self) -> None:
        singleflight = SingleFlight()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def factory() -> list[str]:
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(singleflight.run("only", factory))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), timeout=0.5)

    async def test_one_cancelled_waiter_does_not_cancel_work_needed_by_other_waiter(self) -> None:
        singleflight = SingleFlight()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def factory() -> list[str]:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return ["ok"]

        first = asyncio.create_task(singleflight.run("shared", factory))
        second = asyncio.create_task(singleflight.run("shared", factory))
        await started.wait()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertEqual(await second, ["ok"])
        self.assertEqual(calls, 1)


class CircuitBreakerCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        reset_search_runtimes()

    async def asyncTearDown(self) -> None:
        await close_search_runtimes()

    async def test_cancelled_half_open_probe_releases_probe_slot(self) -> None:
        settings = Settings(
            _env_file=None,
            search_circuit_failure_threshold=1,
        )
        runtime = get_search_runtime(settings)
        key = BackendKey(namespace=id(settings), backend="searxng", kind="web")
        runtime.breaker.record_failure(key, FailureKind.TIMEOUT, "seed open circuit")
        runtime.breaker._entries[key].cooldown_until = 0.0

        async def cancelled_call(_query: str, _settings: Settings, _limit: int) -> list[dict]:
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await router._attempt(
                name="searxng",
                kind="web",
                query="cancel-half-open",
                settings=settings,
                limit=8,
                deadline=asyncio.get_running_loop().time() + settings.search_total_timeout_sec,
                configured_timeout=settings.searxng_timeout_sec,
                call=cancelled_call,
                attempted=set(),
            )

        self.assertTrue(
            runtime.breaker.allow_request(key),
            "a cancelled half-open probe must not permanently occupy the probe slot",
        )


class SearchRuntimeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        reset_search_runtimes()

    async def asyncTearDown(self) -> None:
        await close_search_runtimes()

    async def test_shared_http_client_is_reused_and_closed(self) -> None:
        settings = Settings(_env_file=None, searxng_timeout_sec=6)
        client = AsyncMock()
        with patch("app.search.runtime.httpx.AsyncClient", return_value=client) as ctor:
            runtime = get_search_runtime(settings)
            first = runtime.get_http_client()
            second = runtime.get_http_client()
            self.assertIs(first, client)
            self.assertIs(second, client)
            ctor.assert_called_once_with(timeout=6)
            await close_search_runtimes()
        client.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
