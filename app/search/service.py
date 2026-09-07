"""Web search service — backend ddgs | searxng | tavily."""
from __future__ import annotations

import logging

from ..config import Settings

logger = logging.getLogger(__name__)

MAX_RESULTS = 8


class SearchError(Exception):
    pass


async def search(query: str, settings: Settings, limit: int = MAX_RESULTS) -> list[dict]:
    """Tìm kiếm web, trả list dict {title, url, snippet}.

    Ném SearchError kèm thông báo tiếng Việt cho model khi mọi backend đều lỗi.
    """
    backend = settings.search_backend.strip().lower()
    try:
        if backend == "searxng":
            from .searxng_backend import search_searxng

            return await search_searxng(query, settings, limit)
        if backend == "tavily":
            from .tavily_backend import search_tavily

            return await search_tavily(query, settings, limit)
        # mặc định: ddgs
        from .ddgs_backend import search_ddgs

        return await search_ddgs(query, settings, limit)
    except Exception as exc:
        logger.warning("Search backend '%s' lỗi: %s", backend, exc)
        raise SearchError(
            f"Không tìm kiếm được web (backend {backend} lỗi: {exc}). "
            "Trả lời dựa trên kiến thức và nói rõ là không có dữ liệu mới."
        ) from exc
