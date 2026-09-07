"""Backend Tavily API (free ~1.000 credit/tháng) — chất lượng cao, tùy chọn."""

from __future__ import annotations

import logging

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)


async def search_tavily(query: str, settings: Settings, limit: int) -> list[dict]:
    if not settings.tavily_api_key:
        raise RuntimeError("TAVILY_API_KEY chưa được cấu hình")
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    results: list[dict] = []
    for item in (data.get("results") or [])[:limit]:
        url_item = item.get("url") or ""
        title = item.get("title") or ""
        content = item.get("content") or ""
        if url_item:
            results.append({"title": title[:300], "url": url_item, "snippet": content[:400]})
    return results
