"""Web search service — unified backend auto | searxng | ddgs."""

from __future__ import annotations

import logging

from ..config import Settings

logger = logging.getLogger(__name__)

MAX_RESULTS = 8


class SearchError(Exception):
    pass


async def search(query: str, settings: Settings, limit: int = MAX_RESULTS) -> list[dict]:
    """Tìm kiếm web, trả list dict {title, url, snippet}."""
    backend = settings.search_backend.strip().lower()

    if backend == "searxng":
        from .searxng_backend import search_searxng

        try:
            return await search_searxng(query, settings, limit)
        except Exception as exc:  # noqa: BLE001
            raise _search_error(backend, exc) from exc

    if backend == "ddgs":
        from .ddgs_backend import search_ddgs

        try:
            return await search_ddgs(query, settings, limit)
        except Exception as exc:  # noqa: BLE001
            raise _search_error(backend, exc) from exc

    from .ddgs_backend import search_ddgs
    from .router import RoutedSearchError, route_auto
    from .searxng_backend import search_searxng

    try:
        return await route_auto(
            query,
            settings,
            limit,
            kind="web",
            result_url_key="url",
            searx_call=search_searxng,
            ddgs_call=search_ddgs,
        )
    except RoutedSearchError as exc:
        raise SearchError(
            "Không tìm kiếm được web từ các backend miễn phí hiện tại. "
            "Trả lời dựa trên kiến thức và nói rõ là không có dữ liệu mới."
        ) from exc


def _search_error(backend: str, exc: Exception) -> SearchError:
    logger.warning("Search backend '%s' lỗi: %s", backend, exc)
    return SearchError(
        f"Không tìm kiếm được web (backend {backend} lỗi: {exc}). "
        "Trả lời dựa trên kiến thức và nói rõ là không có dữ liệu mới."
    )
