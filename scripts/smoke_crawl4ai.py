"""Opt-in live smoke for the private Crawl4AI service.

Run inside the bot container after setting CRAWL4AI_URL and CRAWL4AI_API_TOKEN.
This script is intentionally not imported or executed by ordinary CI.
"""

from __future__ import annotations

import asyncio
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import httpx

from app.search.crawl4ai_client import _crawl_payload
from app.search.reader import validate_public_url


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
) -> float:
    started = time.perf_counter()
    response = await client.post(
        f"{base_url.rstrip('/')}/crawl",
        headers={"Authorization": f"Bearer {token}"},
        json=_crawl_payload(url, proxy_url),
    )
    elapsed = time.perf_counter() - started
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise RuntimeError("Crawl4AI returned a non-object response")
    results = body.get("results") if isinstance(body, dict) else None
    if not body.get("success") or not isinstance(results, list) or not results:
        raise RuntimeError("Crawl4AI returned no successful results")
    item = results[0]
    if not isinstance(item, dict) or item.get("success") is False:
        raise RuntimeError("Crawl4AI first result failed")
    for key in ("url", "redirected_url"):
        destination = item.get(key)
        if destination is not None and (
            not isinstance(destination, str) or validate_public_url(destination) is not None
        ):
            raise RuntimeError("Crawl4AI returned a forbidden destination")
    text = _markdown_text(item)
    if not text:
        raise RuntimeError("Crawl4AI returned empty Markdown")
    print(f"crawl ok url={url} seconds={elapsed:.3f} chars={len(text)}")
    return elapsed


def load_urls(path: Path) -> list[str]:
    """Require 20 distinct, public, manually selected URLs for the staging gate."""
    urls = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]
    if len(urls) < 20 or len(set(urls)) != len(urls):
        raise ValueError("smoke manifest requires at least 20 distinct URLs")
    for url in urls:
        if validate_public_url(url) is not None:
            raise ValueError("smoke manifest contains a blocked or malformed URL")
    return urls


def report_timings(durations: list[float]) -> None:
    ordered = sorted(durations)
    p50 = ordered[math.ceil(len(ordered) * 0.50) - 1]
    p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
    print(f"smoke passed={len(ordered)} p50={p50:.3f}s p95={p95:.3f}s "
          f"min={ordered[0]:.3f}s max={ordered[-1]:.3f}s")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls-file", type=Path,
                        help="Staging gate: at least 20 unique public URLs, one per line")
    args = parser.parse_args()
    base_url = os.getenv("CRAWL4AI_URL", "http://crawl4ai:11235").strip()
    token = os.getenv("CRAWL4AI_API_TOKEN", "").strip()
    proxy_url = os.getenv("CRAWL4AI_EGRESS_PROXY_URL", "").strip()
    isolated = (base_url, proxy_url) == (
        "http://crawl4ai:11235", "http://crawl4ai-proxy:3128"
    )
    if not isolated or not token:
        print("Crawl4AI isolated endpoint, proxy and token required", file=sys.stderr)
        return 2

    try:
        urls = load_urls(args.urls_file) if args.urls_file else ["https://example.com"]
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"Invalid smoke manifest: {exc}", file=sys.stderr)
        return 2
    if not args.urls_file:
        js_url = os.getenv("CRAWL4AI_SMOKE_JS_URL", "").strip()
        if js_url:
            if validate_public_url(js_url) is not None:
                print("Invalid JS smoke URL", file=sys.stderr)
                return 2
            urls.append(js_url)

    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        started = time.perf_counter()
        health = await client.get(f"{base_url.rstrip('/')}/health")
        health.raise_for_status()
        print(f"health ok seconds={time.perf_counter() - started:.3f}")
        durations = []
        for url in urls:
            try:
                durations.append(await _crawl(client, base_url, token, url, proxy_url))
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                print(f"smoke FAILED after={len(durations)} url={url} "
                      f"reason={type(exc).__name__}", file=sys.stderr)
                return 1
        report_timings(durations)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
