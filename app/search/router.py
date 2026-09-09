"""Bounded resilient routing for auto web/image search."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..config import Settings
from .resilience import BackendKey, FailureKind
from .runtime import get_search_runtime

logger = logging.getLogger(__name__)

_MIN_RESULTS_BEFORE_TOPUP = 2


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
    except ValueError:
        return raw
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        return raw
    port = parts.port
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _merge_results(current: list[dict], incoming: list[dict], *, key_name: str, limit: int) -> list[dict]:
    out = [dict(item) for item in current]
    seen = {_canonical_url(item.get(key_name)) for item in out}
    for item in incoming:
        marker = _canonical_url(item.get(key_name))
        if not marker or marker in seen:
            continue
        seen.add(marker)
        out.append(dict(item))
        if len(out) >= limit:
            break
    return out[:limit]


def _classify_failure(exc: BaseException) -> FailureKind:
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


async def _attempt(
    *,
    name: str,
    kind: str,
    query: str,
    settings: Settings,
    limit: int,
    deadline: float,
    configured_timeout: float,
    call: Callable[[str, Settings, int], Awaitable[list[dict]]],
    attempted: set[str],
) -> tuple[bool, list[dict], BaseException | None]:
    if name in attempted:
        return False, [], RuntimeError(f"backend {name} already attempted")
    attempted.add(name)

    runtime = get_search_runtime(settings)
    key = BackendKey(namespace=id(settings), backend=name, kind=kind)
    if not runtime.breaker.allow_request(key):
        return False, [], None

    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        exc = asyncio.TimeoutError("search total deadline exhausted")
        runtime.breaker.record_failure(key, FailureKind.TIMEOUT, str(exc))
        return True, [], exc

    timeout = min(configured_timeout, remaining)
    try:
        results = await asyncio.wait_for(call(query, settings, limit), timeout=timeout)
        if not isinstance(results, list):
            raise RuntimeError(f"backend {name} returned non-list results")
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        kind_failure = _classify_failure(exc)
        runtime.breaker.record_failure(key, kind_failure, str(exc))
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
) -> list[dict]:
    runtime = get_search_runtime(settings)
    normalized = _normalize_query(query)
    cache_key = f"{kind}:{limit}:{normalized}"

    fresh = runtime.cache.get_fresh(cache_key)
    if fresh is not None:
        return fresh[:limit]

    async def execute() -> list[dict]:
        # A second cache check prevents a queued singleflight creator from repeating work.
        second_fresh = runtime.cache.get_fresh(cache_key)
        if second_fresh is not None:
            return second_fresh[:limit]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + settings.search_total_timeout_sec
        attempted: set[str] = set()
        merged: list[dict] = []
        had_successful_backend = False
        failures: list[str] = []

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
            )
            if attempted_now and error is None:
                had_successful_backend = True
                merged = _merge_results(merged, results, key_name=result_url_key, limit=limit)
                if len(merged) >= _MIN_RESULTS_BEFORE_TOPUP:
                    _cache_success(runtime, cache_key, merged, settings, kind)
                    return merged
            elif error is not None:
                failures.append(f"searxng:{type(error).__name__}")
                logger.warning("SearXNG %s search lỗi, fallback DDGS: %s", kind, error)

        if len(merged) < _MIN_RESULTS_BEFORE_TOPUP:
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
            )
            if attempted_now and error is None:
                had_successful_backend = True
                merged = _merge_results(merged, results, key_name=result_url_key, limit=limit)
            elif error is not None:
                failures.append(f"ddgs:{type(error).__name__}")
                logger.warning("DDGS %s search lỗi: %s", kind, error)

        if merged:
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

    return await runtime.singleflight.run(cache_key, execute)


def _cache_success(runtime, key: str, results: list[dict], settings: Settings, kind: str) -> None:
    fresh_ttl = (
        settings.search_image_cache_ttl_sec if kind == "image" else settings.search_web_cache_ttl_sec
    )
    runtime.cache.set(
        key,
        results,
        fresh_ttl_sec=fresh_ttl,
        stale_ttl_sec=settings.search_stale_cache_ttl_sec,
    )
