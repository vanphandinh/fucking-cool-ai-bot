"""Process-local search runtime state shared across routed searches."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import Settings
from .resilience import CircuitBreaker, SearchCache, SingleFlight


@dataclass
class SearchRuntime:
    settings: Settings
    breaker: CircuitBreaker
    cache: SearchCache
    singleflight: SingleFlight
    http_client: httpx.AsyncClient | None = None

    def get_http_client(self) -> httpx.AsyncClient:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient()
        return self.http_client

    async def aclose(self) -> None:
        client = self.http_client
        self.http_client = None
        if client is not None:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


_runtimes: dict[int, SearchRuntime] = {}


def get_search_runtime(settings: Settings) -> SearchRuntime:
    key = id(settings)
    current = _runtimes.get(key)
    if current is not None and current.settings is settings:
        return current

    runtime = SearchRuntime(
        settings=settings,
        breaker=CircuitBreaker(
            failure_threshold=settings.search_circuit_failure_threshold,
            cooldown_sec=settings.search_circuit_cooldown_sec,
            rate_limit_cooldown_sec=settings.search_rate_limit_cooldown_sec,
        ),
        cache=SearchCache(max_entries=settings.search_cache_max_entries),
        singleflight=SingleFlight(),
    )
    _runtimes[key] = runtime
    return runtime


async def close_search_runtimes() -> None:
    runtimes = tuple(_runtimes.values())
    _runtimes.clear()
    for runtime in runtimes:
        await runtime.aclose()


def reset_search_runtimes() -> None:
    """Test hook for runtimes that never allocated an HTTP client."""
    _runtimes.clear()
