"""Backend Tavily API (free ~1.000 credit/tháng) — chất lượng cao, tùy chọn."""

from __future__ import annotations

import logging

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)


async def search_tavily(query: str, settings: Settings, limit: int) -> list[dict]:
    if not settings.tavily_api_key:
        raise RuntimeError("TAVILY_API_KEY chưa được cấu hình")
    timeout = max(5.0, min(float(settings.request_timeout_sec), 30.0))
    async with httpx.AsyncClient(timeout=timeout) as client:
        # Tavily hiện chỉ chấp nhận key qua header `Authorization: Bearer`
        # (key dev tier từ chối dạng cũ `api_key` trong body) — theo docs chính thức.
        resp = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            json={
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
            },
        )
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("Tavily trả JSON không phải object")
    results: list[dict] = []
    for item in (data.get("results") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        url_item = item.get("url") or ""
        title = item.get("title") or ""
        content = item.get("content") or ""
        if url_item:
            results.append({"title": title[:300], "url": url_item, "snippet": content[:400]})
    return results
