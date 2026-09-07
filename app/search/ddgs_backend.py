"""Backend DuckDuckGo qua thư viện ddgs (không cần key)."""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings

logger = logging.getLogger(__name__)


def _ddgs_search_sync(query: str, limit: int) -> list[dict]:
    from ddgs import DDGS

    with DDGS(timeout=15) as ddgs:
        raw = list(ddgs.text(query, max_results=limit, region="vn-vi"))
    results: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or ""
        url = item.get("href") or item.get("url") or ""
        snippet = item.get("body") or item.get("content") or item.get("description") or ""
        if url:
            results.append({"title": title[:300], "url": url, "snippet": snippet[:400]})
    return results


async def search_ddgs(query: str, settings: Settings, limit: int) -> list[dict]:
    timeout = max(5.0, float(settings.request_timeout_sec))
    return await asyncio.wait_for(
        asyncio.to_thread(_ddgs_search_sync, query, limit),
        timeout=timeout,
    )
