"""Backend DuckDuckGo qua thư viện ddgs (không cần key)."""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings

logger = logging.getLogger(__name__)


def _http_url(value: object) -> str:
    url = str(value or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


def _ddgs_search_sync(query: str, limit: int, timeout_sec: float = 15.0) -> list[dict]:
    from ddgs import DDGS

    with DDGS(timeout=timeout_sec) as ddgs:
        raw = list(ddgs.text(query, max_results=limit, region="vn-vi"))
    results: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "")
        url = _http_url(item.get("href") or item.get("url"))
        snippet = str(
            item.get("body") or item.get("content") or item.get("description") or ""
        )
        if url:
            results.append({"title": title[:300], "url": url, "snippet": snippet[:400]})
    return results


def _ddgs_image_search_sync(query: str, limit: int, timeout_sec: float = 15.0) -> list[dict]:
    from ddgs import DDGS

    with DDGS(timeout=timeout_sec) as ddgs:
        raw = list(
            ddgs.images(
                query,
                max_results=limit,
                region="vn-vi",
                safesearch="moderate",
            )
        )
    results: list[dict] = []
    seen: set[str] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        image_url = _http_url(item.get("image") or item.get("img_src"))
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        results.append(
            {
                "title": str(item.get("title") or "")[:300],
                "image_url": image_url,
                "thumbnail_url": _http_url(item.get("thumbnail")),
                "page_url": _http_url(item.get("url") or item.get("href")),
                "source": str(item.get("source") or "")[:100],
            }
        )
        if len(results) >= limit:
            break
    return results


async def search_ddgs(query: str, settings: Settings, limit: int) -> list[dict]:
    timeout = float(settings.ddgs_timeout_sec)
    return await asyncio.wait_for(
        asyncio.to_thread(_ddgs_search_sync, query, limit, timeout),
        timeout=timeout,
    )


async def search_ddgs_images(query: str, settings: Settings, limit: int) -> list[dict]:
    timeout = float(settings.ddgs_timeout_sec)
    return await asyncio.wait_for(
        asyncio.to_thread(_ddgs_image_search_sync, query, limit, timeout),
        timeout=timeout,
    )
