"""Bounded resilient routing for auto web/image search."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..config import Settings
from ..core.job_operations import OperationBudget
from .resilience import BackendKey, FailureKind
from .runtime import get_search_runtime
from .source_ranking import has_independent_coverage, rank_web_results, verification_intent

logger = logging.getLogger(__name__)

_MIN_WEB_RESULTS_BEFORE_TOPUP = 2
_MIN_IMAGE_RESULTS_BEFORE_TOPUP = 1
# Merge a deeper candidate pool before ranking so a strong DDGS result is not
# dropped by the cap just because SearXNG returned a full page of weak hits.
_MERGE_POOL_FACTOR = 3


class RoutedSearchError(RuntimeError):
    pass


def _normalize_query(query: str) -> str:
    return " ".join((query or "").split())


def _canonical_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return raw
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return raw
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _merge_results(
    current: list[dict],
    incoming: list[dict],
    *,
    key_name: str,
    limit: int,
    keep_duplicate_excerpts: bool = False,
) -> list[dict]:
    """Merge one backend batch into the candidate pool.

    ``keep_duplicate_excerpts`` is the V2 web path only: alternate excerpts of
    an already-seen canonical URL are kept so ranking can prefer the more
    informative one. V1 web and image search keep first-hit wins.

    On the V2 path distinct URLs are always placed ahead of those alternate
    excerpts. The pool is a fixed budget, so ordering matters: a backend that
    repeats one page many times must not consume the room a still-unseen, more
    relevant page from the second backend needs. Alternate excerpts only fill
    whatever capacity the distinct URLs leave over.
    """
    if limit <= 0:
        return []
    out = [dict(item) for item in current]
    seen = {_canonical_url(item.get(key_name)) for item in out}
    if not keep_duplicate_excerpts:
        for item in incoming:
            marker = _canonical_url(item.get(key_name))
            if not marker or marker in seen:
                continue
            seen.add(marker)
            out.append(dict(item))
            if len(out) >= limit:
                break
        return out[:limit]

    distinct: list[dict] = []
    extras: list[dict] = []
    seen = set()
    for item in (*out, *incoming):
        marker = _canonical_url(item.get(key_name))
        if not marker:
            continue
        if marker in seen:
            if len(distinct) + len(extras) < limit:
                extras.append(dict(item))
            continue
        seen.add(marker)
        if len(distinct) < limit:
            distinct.append(dict(item))
    return (distinct + extras)[:limit]


def _meets_minimum(
    *,
    quality_gate: bool,
    query: str,
    results: list[dict],
    min_results: int,
    verification_context: bool = False,
) -> bool:
    """Decide whether an early backend result set is good enough to stop.

    The V2 web policy accepts a backend only when at least two independent
    domains carry a lexically relevant result (>= 0.20). Image routing and the
    flag-off V1 path keep the original count-based threshold.
    """
    if quality_gate:
        # Independent keyword matches can agree on an obsolete or false claim.
        # On verification queries, try DDGS as a second discovery route even
        # when SearXNG returned two relevant domains.
        if verification_context:
            return False
        return has_independent_coverage(query, results)
    return len(results) >= min_results


def _classify_failure(exc: Exception) -> FailureKind:
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return FailureKind.TIMEOUT
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, OSError)):
        return FailureKind.CONNECTION
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return FailureKind.RATE_LIMIT
        if status >= 500:
            return FailureKind.SERVER_ERROR
    text = str(exc).lower()
    if "429" in text or "too many request" in text or "rate limit" in text:
        return FailureKind.RATE_LIMIT
    return FailureKind.INVALID_RESPONSE


def _safe_failure_summary(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTPStatusError:{exc.response.status_code}"
    return type(exc).__name__


async def _attempt(
    *,
    name: str,
    kind: str,
    query: str,
    settings: Settings,
    limit: int,
    deadline: float | None,
    configured_timeout: float,
    call: Callable[[str, Settings, int], Awaitable[list[dict]]],
    attempted: set[str],
    operations=None,
    budget: OperationBudget | None = None,
) -> tuple[bool, list[dict], Exception | None]:
    if name in attempted:
        return False, [], RuntimeError(f"backend {name} already attempted")
    attempted.add(name)

    runtime = get_search_runtime(settings)
    key = BackendKey(namespace=runtime.namespace, backend=name, kind=kind)
    if not runtime.breaker.allow_request(key):
        return False, [], None

    if operations is None:
        assert deadline is not None
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            exc = asyncio.TimeoutError("search total deadline exhausted")
            runtime.breaker.record_failure(
                key,
                FailureKind.TIMEOUT,
                _safe_failure_summary(exc),
            )
            return True, [], exc
        timeout = min(configured_timeout, remaining)
    else:
        timeout = configured_timeout

    try:
        if operations is None:
            results = await asyncio.wait_for(call(query, settings, limit), timeout=timeout)
        else:
            results = await operations.run(
                f"{name} {kind} search",
                lambda: call(query, settings, limit),
                timeout_sec=timeout,
                budget=budget,
            )
        if not isinstance(results, list):
            raise RuntimeError(f"backend {name} returned non-list results")
    except asyncio.CancelledError:
        runtime.breaker.record_cancelled(key)
        raise
    except Exception as exc:
        kind_failure = _classify_failure(exc)
        runtime.breaker.record_failure(key, kind_failure, _safe_failure_summary(exc))
        return True, [], exc

    runtime.breaker.record_success(key)
    return True, results, None


async def route_auto(
    query: str,
    settings: Settings,
    limit: int,
    *,
    kind: str,
    result_url_key: str,
    searx_call: Callable[[str, Settings, int], Awaitable[list[dict]]] | None,
    ddgs_call: Callable[[str, Settings, int], Awaitable[list[dict]]],
    operations=None,
    verification_context: bool = False,
) -> list[dict]:
    if kind == "web":
        # Enforce the web cap even for direct router callers. Images have their
        # own, independent limit policy.
        from .service import effective_limit

        limit = effective_limit(limit)
        if limit == 0:
            return []
    runtime = get_search_runtime(settings)
    normalized = _normalize_query(query)
    # Ranking and the independence gate change web ordering, so V1 and V2
    # answers for the same query must never share a cache entry.
    quality_gate = kind == "web" and settings.web_evidence_v2_enabled
    verify = quality_gate and (verification_context or verification_intent(normalized))
    cache_key = f"{kind}:{limit}:{normalized}" + (":v2" if quality_gate else "")
    if verify:
        cache_key += ":verify"

    fresh = runtime.cache.get_fresh(cache_key)
    if fresh is not None:
        return fresh[:limit]

    async def execute() -> list[dict]:
        second_fresh = runtime.cache.get_fresh(cache_key)
        if second_fresh is not None:
            return second_fresh[:limit]

        loop = asyncio.get_running_loop()
        deadline = (
            None
            if operations is not None
            else loop.time() + settings.search_total_timeout_sec
        )
        operation_budget = (
            OperationBudget(settings.search_total_timeout_sec) if operations is not None else None
        )
        # The V2 web path ranks the merged pool and then truncates to ``limit``;
        # V1 and image keep their original merge cap.
        pool_limit = limit if not quality_gate else max(limit, limit * _MERGE_POOL_FACTOR)
        attempted: set[str] = set()
        merged: list[dict] = []
        had_successful_backend = False
        searxng_sufficient = False
        failures: list[str] = []
        min_results = (
            _MIN_IMAGE_RESULTS_BEFORE_TOPUP
            if kind == "image"
            else _MIN_WEB_RESULTS_BEFORE_TOPUP
        )

        if searx_call is not None and settings.searxng_url.strip():
            attempted_now, results, error = await _attempt(
                name="searxng",
                kind=kind,
                query=query,
                settings=settings,
                limit=limit,
                deadline=deadline,
                configured_timeout=settings.searxng_timeout_sec,
                call=searx_call,
                attempted=attempted,
                operations=operations,
                budget=operation_budget,
            )
            if attempted_now and error is None:
                had_successful_backend = True
                merged = _merge_results(
                    merged,
                    results,
                    key_name=result_url_key,
                    limit=pool_limit,
                    keep_duplicate_excerpts=quality_gate,
                )
                searxng_sufficient = _meets_minimum(
                    quality_gate=quality_gate,
                    query=normalized,
                    results=merged,
                    min_results=min_results,
                    verification_context=verify,
                )
                if searxng_sufficient:
                    if quality_gate:
                        merged = rank_web_results(
                            normalized, merged, limit=limit, verification_context=verify
                        )
                    _cache_success(runtime, cache_key, merged, settings, kind)
                    return merged
            elif error is not None:
                summary = _safe_failure_summary(error)
                failures.append(f"searxng:{summary}")
                logger.warning(
                    "SearXNG %s search lỗi, fallback DDGS (%s)",
                    kind,
                    summary,
                )

        if not searxng_sufficient:
            attempted_now, results, error = await _attempt(
                name="ddgs",
                kind=kind,
                query=query,
                settings=settings,
                limit=limit,
                deadline=deadline,
                configured_timeout=settings.ddgs_timeout_sec,
                call=ddgs_call,
                attempted=attempted,
                operations=operations,
                budget=operation_budget,
            )
            if attempted_now and error is None:
                had_successful_backend = True
                merged = _merge_results(
                    merged,
                    results,
                    key_name=result_url_key,
                    limit=pool_limit,
                    keep_duplicate_excerpts=quality_gate,
                )
            elif error is not None:
                summary = _safe_failure_summary(error)
                failures.append(f"ddgs:{summary}")
                logger.warning("DDGS %s search lỗi (%s)", kind, summary)

        if merged:
            if quality_gate:
                merged = rank_web_results(
                    normalized, merged, limit=limit, verification_context=verify
                )
            _cache_success(runtime, cache_key, merged, settings, kind)
            return merged
        if had_successful_backend:
            return []

        stale = runtime.cache.get_stale(cache_key)
        if stale is not None:
            logger.warning("Dùng stale search cache (%s) sau khi backend unavailable", kind)
            return stale[:limit]

        reason = ", ".join(failures) if failures else "all search backends unavailable"
        raise RoutedSearchError(reason)

    # A controlled job cannot safely share one executing coroutine with another
    # owner's consent state. Cache remains shared, but live singleflight ownership
    # is bypassed while renewable controls are active.
    if operations is not None:
        return await execute()
    return await runtime.singleflight.run(cache_key, execute)


def _cache_success(runtime, key: str, results: list[dict], settings: Settings, kind: str) -> None:
    if kind == "image":
        fresh_ttl = settings.search_image_cache_ttl_sec
    else:
        fresh_ttl = settings.search_web_cache_ttl_sec
    runtime.cache.set(
        key,
        results,
        fresh_ttl_sec=fresh_ttl,
        stale_ttl_sec=settings.search_stale_cache_ttl_sec,
    )
