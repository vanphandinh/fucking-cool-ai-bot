"""Bounded authenticated client for the private Crawl4AI URL reader."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from ..config import Settings
from . import reader


@dataclass(frozen=True)
class Crawl4AIReadResult:
    text: str
    source_url: str
    ok: bool
    reason: str = ""


_client: httpx.AsyncClient | None = None
_client_timeout: float | None = None
_client_lock = asyncio.Lock()


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def _html_to_text(raw: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(raw)
    except Exception:
        return ""
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def _failed(url: str, reason: str) -> Crawl4AIReadResult:
    return Crawl4AIReadResult("", url, False, reason)


async def _get_client(settings: Settings) -> httpx.AsyncClient:
    global _client, _client_timeout
    timeout = float(settings.crawl4ai_timeout_sec)
    if _client is not None and _client_timeout == timeout:
        return _client
    async with _client_lock:
        if _client is not None and _client_timeout == timeout:
            return _client
        old = _client
        _client = httpx.AsyncClient(timeout=timeout, trust_env=False)
        _client_timeout = timeout
    if old is not None:
        await old.aclose()
    return _client


async def close_crawl4ai_client() -> None:
    global _client, _client_timeout
    async with _client_lock:
        client = _client
        _client = None
        _client_timeout = None
    if client is not None:
        await client.aclose()


def _extract_markdown(value: object) -> str:
    if isinstance(value, dict):
        text = value.get("fit_markdown") or value.get("raw_markdown") or ""
        return str(text).strip()
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                return text
            if isinstance(parsed, dict):
                return _extract_markdown(parsed)
        return text
    return ""


async def read_page(url: str, settings: Settings) -> Crawl4AIReadResult:
    """Read one public URL through Crawl4AI without retries or user-controlled config."""
    source_url = url
    client = await _get_client(settings)
    payload = {
        "urls": [url],
        "browser_config": {},
        "crawler_config": {},
    }
    headers = {"Authorization": f"Bearer {settings.crawl4ai_api_token}"}
    endpoint = f"{settings.crawl4ai_url.rstrip('/')}/crawl"

    try:
        response = await client.post(endpoint, headers=headers, json=payload)
    except asyncio.CancelledError:
        raise
    except httpx.TimeoutException:
        return _failed(source_url, "timeout")
    except httpx.RequestError:
        return _failed(source_url, "network")
    except Exception:
        return _failed(source_url, "client_error")

    if response.status_code in (401, 403):
        return _failed(source_url, "auth")
    if response.status_code == 429:
        return _failed(source_url, "rate_limited")
    if response.status_code >= 500:
        return _failed(source_url, "upstream_5xx")
    if response.status_code >= 400:
        return _failed(source_url, f"http_{response.status_code}")

    try:
        body = response.json()
    except ValueError:
        return _failed(source_url, "malformed_json")
    if not isinstance(body, dict):
        return _failed(source_url, "malformed_response")

    results = body.get("results")
    if not body.get("success") or not isinstance(results, list) or not results:
        return _failed(source_url, "malformed_response")
    item = results[0]
    if not isinstance(item, dict):
        return _failed(source_url, "malformed_response")
    if item.get("success") is False:
        return _failed(source_url, "crawl_failed")

    text = _extract_markdown(item.get("markdown"))
    if not text:
        cleaned_html = item.get("cleaned_html")
        if isinstance(cleaned_html, str) and cleaned_html.strip():
            text = _html_to_text(cleaned_html)
    if not text:
        return _failed(source_url, "empty_content")

    candidate = item.get("redirected_url") or item.get("url")
    if isinstance(candidate, str) and candidate.strip() and reader.validate_public_url(candidate.strip()) is None:
        source_url = candidate.strip()

    return Crawl4AIReadResult(
        text=text[: settings.crawl4ai_max_chars],
        source_url=source_url,
        ok=True,
    )
