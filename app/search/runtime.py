"""Process-local search runtime state shared across routed searches."""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import httpx

from ..config import Settings
from .resilience import CircuitBreaker, SearchCache, SingleFlight

# Settings fields the runtime's collaborators are built from. Two Settings
# objects with equal values describe the same runtime.
_RUNTIME_SETTING_FIELDS = (
    "searxng_url",
    "searxng_timeout_sec",
    "search_circuit_failure_threshold",
    "search_circuit_cooldown_sec",
    "search_rate_limit_cooldown_sec",
    "search_cache_max_entries",
)

RuntimeKey = tuple[object, ...]


@dataclass
class SearchRuntime:
    settings: Settings
    breaker: CircuitBreaker
    cache: SearchCache
    singleflight: SingleFlight
    # Stable breaker namespace: replaces ``id(settings)`` so breaker entries do
    # not accumulate once one runtime serves many per-chat Settings copies.
    namespace: int
    http_client: httpx.AsyncClient | None = None

    def get_http_client(self) -> httpx.AsyncClient:
        if self.http_client is None:
            self.http_client = httpx.AsyncClient(timeout=self.settings.searxng_timeout_sec)
        return self.http_client

    async def aclose(self) -> None:
        client = self.http_client
        self.http_client = None
        if client is not None:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


_runtimes: dict[RuntimeKey, SearchRuntime] = {}
_next_namespace = itertools.count(1)


def _runtime_key(settings: Settings) -> RuntimeKey:
    return tuple(getattr(settings, field) for field in _RUNTIME_SETTING_FIELDS)


def get_search_runtime(settings: Settings) -> SearchRuntime:
    """Return the process-local runtime for this search configuration.

    Settings instances are value objects: two of them with the same search
    configuration describe the same runtime. Keying by the values that shape the
    runtime keeps breaker, cache and single-flight state shared instead of
    creating a new runtime per instance; ``runtime.settings`` retains only the
    first Settings object seen for a configuration.
    """
    key = _runtime_key(settings)
    current = _runtimes.get(key)
    if current is not None:
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
        namespace=next(_next_namespace),
    )
    _runtimes[key] = runtime
    return runtime


async def close_search_runtimes() -> None:
    runtimes = tuple(_runtimes.values())
    _runtimes.clear()
    errors: list[Exception] = []
    for runtime in runtimes:
        try:
            await runtime.aclose()
        except Exception as exc:
            errors.append(exc)
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise ExceptionGroup("Search runtime cleanup failed", errors)


def reset_search_runtimes() -> None:
    """Test hook for runtimes that never allocated an HTTP client."""
    _runtimes.clear()
