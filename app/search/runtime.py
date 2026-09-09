"""Process-local search runtime state shared across routed searches."""

from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings
from .resilience import CircuitBreaker, SearchCache, SingleFlight


@dataclass
class SearchRuntime:
    settings: Settings
    breaker: CircuitBreaker
    cache: SearchCache
    singleflight: SingleFlight


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


def reset_search_runtimes() -> None:
    """Test/maintenance hook: clear in-process health/cache state."""
    _runtimes.clear()
