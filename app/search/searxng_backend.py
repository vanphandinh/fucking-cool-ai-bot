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


def _http_url(value: object) -> str:
    url = str(value or "").strip()
    return url if url.startswith(("http://", "https://")) else ""


async def _request(query: str, settings: Settings, params: dict[str, str]) -> dict:
    url = (settings.searxng_url or "").rstrip("/")
    if not url:
        raise RuntimeError("SEARXNG_URL chưa được cấu hình")
    timeout = float(settings.searxng_timeout_sec)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{url}/search",
            params={"q": query, "format": "json", **params},
            headers={"User-Agent": _UA, "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("SearXNG trả JSON không phải object")
    if unresponsive := data.get("unresponsive_engines"):
        logger.warning("SearXNG có engine lỗi (unresponsive_engines=%s)", unresponsive)
    return data


async def search_searxng(query: str, settings: Settings, limit: int) -> list[dict]:
    data = await _request(query, settings, {})
    results: list[dict] = []
    raw_results = data.get("results") or []
    if not isinstance(raw_results, list):
        raise RuntimeError("SearXNG trả trường results không phải list")
    for item in raw_results[:limit]:
        if not isinstance(item, dict):
            continue
        url_item = _http_url(item.get("url"))
        title = str(item.get("title") or "")
        content = str(item.get("content") or item.get("snippet") or "")
        if url_item:
            results.append({"title": title[:300], "url": url_item, "snippet": content[:400]})
    return results


async def search_searxng_images(query: str, settings: Settings, limit: int) -> list[dict]:
    data = await _request(query, settings, {"categories": "images", "safesearch": "1"})
    raw_results = data.get("results") or []
    if not isinstance(raw_results, list):
        raise RuntimeError("SearXNG trả trường results không phải list")

    results: list[dict] = []
    seen: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        image_url = _http_url(item.get("img_src") or item.get("image"))
        if not image_url or image_url in seen:
            continue
        seen.add(image_url)
        results.append(
            {
                "title": str(item.get("title") or "")[:300],
                "image_url": image_url,
                "thumbnail_url": _http_url(
                    item.get("thumbnail_src") or item.get("thumbnail")
                ),
                "page_url": _http_url(item.get("url")),
                "source": str(item.get("source") or item.get("engine") or "")[:100],
            }
        )
        if len(results) >= limit:
            break
    return results
