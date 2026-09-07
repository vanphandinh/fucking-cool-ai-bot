"""Backend SearXNG tự host — gọi JSON API (gom nhiều engine, có Google)."""

from __future__ import annotations

import logging

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


async def search_searxng(query: str, settings: Settings, limit: int) -> list[dict]:
    url = (settings.searxng_url or "").rstrip("/")
    if not url:
        raise RuntimeError("SEARXNG_URL chưa được cấu hình")
    timeout = max(5.0, min(float(settings.request_timeout_sec), 30.0))
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{url}/search",
            params={"q": query, "format": "json"},
            headers={"User-Agent": _UA, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("SearXNG trả JSON không phải object")
    results: list[dict] = []
    for item in (data.get("results") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        url_item = item.get("url") or ""
        title = item.get("title") or ""
        content = item.get("content") or item.get("snippet") or ""
        if url_item:
            results.append({"title": title[:300], "url": url_item, "snippet": content[:400]})
    return results
