"""Opt-in live smoke for the private Crawl4AI service.

Run inside the bot container after setting CRAWL4AI_URL and CRAWL4AI_API_TOKEN.
This script is intentionally not imported or executed by ordinary CI.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx

from app.search.crawl4ai_client import _crawl_payload


def _markdown_text(item: dict) -> str:
    markdown = item.get("markdown")
    if isinstance(markdown, dict):
        return str(markdown.get("fit_markdown") or markdown.get("raw_markdown") or "").strip()
    if isinstance(markdown, str):
        value = markdown.strip()
        if value.startswith("{"):
            try:
                parsed = json.loads(value)
            except ValueError:
                return value
            if isinstance(parsed, dict):
                return str(
                    parsed.get("fit_markdown") or parsed.get("raw_markdown") or ""
                ).strip()
        return value
    return ""


async def _crawl(
    client: httpx.AsyncClient, base_url: str, token: str, url: str, proxy_url: str
) -> None:
    started = time.perf_counter()
    response = await client.post(
        f"{base_url.rstrip('/')}/crawl",
        headers={"Authorization": f"Bearer {token}"},
        json=_crawl_payload(url, proxy_url),
    )
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    body = response.json()
    results = body.get("results") if isinstance(body, dict) else None
    if not body.get("success") or not isinstance(results, list) or not results:
        raise RuntimeError("Crawl4AI returned no successful results")
    item = results[0]
    if not isinstance(item, dict) or item.get("success") is False:
        raise RuntimeError("Crawl4AI first result failed")
    text = _markdown_text(item)
    if not text:
        raise RuntimeError("Crawl4AI returned empty Markdown")
    print(f"crawl ok url={url} seconds={elapsed:.3f} chars={len(text)}")


async def main() -> int:
    base_url = os.getenv("CRAWL4AI_URL", "http://crawl4ai:11235").strip()
    token = os.getenv("CRAWL4AI_API_TOKEN", "").strip()
    proxy_url = os.getenv("CRAWL4AI_EGRESS_PROXY_URL", "").strip()
    isolated = (base_url, proxy_url) == (
        "http://crawl4ai:11235", "http://crawl4ai-proxy:3128"
    )
    if not isolated or not token:
        print("Crawl4AI isolated endpoint, proxy and token required", file=sys.stderr)
        return 2

    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        started = time.perf_counter()
        health = await client.get(f"{base_url.rstrip('/')}/health")
        health.raise_for_status()
        print(f"health ok seconds={time.perf_counter() - started:.3f}")
        await _crawl(client, base_url, token, "https://example.com", proxy_url)
        js_url = os.getenv("CRAWL4AI_SMOKE_JS_URL", "").strip()
        if js_url:
            await _crawl(client, base_url, token, js_url, proxy_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
