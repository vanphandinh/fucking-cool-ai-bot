"""Web search service — unified backend auto | searxng | ddgs."""

from __future__ import annotations

import logging

from ..config import Settings
from ..core.job_operations import OperationBudget

logger = logging.getLogger(__name__)

MAX_RESULTS = 8


class SearchError(Exception):
    pass


def effective_limit(limit: object) -> int:
    """Clamp a caller-supplied web-search limit to this public API's cap.

    The cap belongs to the public entry point, not to the callers: an internal
    caller that forgets the policy must not be able to widen the result set
    beyond :data:`MAX_RESULTS`. A non-positive or malformed limit means ``0``
    results and no backend call, which is also what a caller asks for when it
    passes ``limit=0`` (for example, a disabled consumer).

    Only the web search path clamps here; ``route_auto`` callers for images
    keep their own limit handling.
    """
    try:
        value = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return MAX_RESULTS
    if value <= 0:
        return 0
    return min(value, MAX_RESULTS)


async def search(
    query: str,
    settings: Settings,
    limit: int = MAX_RESULTS,
    *,
    operations=None,
    verification_context: bool = False,
) -> list[dict]:
    """Tìm kiếm web, trả list dict {title, url, snippet}."""
    limit = effective_limit(limit)
    if limit <= 0:
        return []
    backend = settings.search_backend.strip().lower()

    if backend == "searxng":
        from .searxng_backend import search_searxng

        try:
            if operations is not None:
                return (await operations.run(
                    "searxng web search",
                    lambda: search_searxng(query, settings, limit),
                    timeout_sec=settings.searxng_timeout_sec,
                    budget=OperationBudget(settings.search_total_timeout_sec),
                ))[:limit]
            return (await search_searxng(query, settings, limit))[:limit]
        except Exception as exc:  # noqa: BLE001
            raise _search_error(backend, exc) from exc

    if backend == "ddgs":
        from .ddgs_backend import search_ddgs

        try:
            if operations is not None:
                return (await operations.run(
                    "ddgs web search",
                    lambda: search_ddgs(query, settings, limit),
                    timeout_sec=settings.ddgs_timeout_sec,
                    budget=OperationBudget(settings.search_total_timeout_sec),
                ))[:limit]
            return (await search_ddgs(query, settings, limit))[:limit]
        except Exception as exc:  # noqa: BLE001
            raise _search_error(backend, exc) from exc

    from .ddgs_backend import search_ddgs
    from .router import RoutedSearchError, route_auto
    from .searxng_backend import search_searxng

    try:
        return (await route_auto(
            query,
            settings,
            limit,
            kind="web",
            result_url_key="url",
            searx_call=search_searxng,
            ddgs_call=search_ddgs,
            operations=operations,
            verification_context=verification_context,
        ))[:limit]
    except RoutedSearchError as exc:
        raise SearchError(
            "Không tìm kiếm được web từ các backend miễn phí hiện tại. "
            "Trả lời dựa trên kiến thức và nói rõ là không có dữ liệu mới."
        ) from exc


def _search_error(backend: str, exc: Exception) -> SearchError:
    error_type = type(exc).__name__
    logger.warning("Search backend '%s' lỗi (%s)", backend, error_type)
    return SearchError(
        f"Không tìm kiếm được web (backend {backend} lỗi: {error_type}). "
        "Trả lời dựa trên kiến thức và nói rõ là không có dữ liệu mới."
    )
