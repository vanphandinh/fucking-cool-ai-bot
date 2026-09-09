"""Unified URL reader: X resolver first, Crawl4AI next, generic reader last."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..config import Settings
from . import crawl4ai_client, reader, x_reader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UrlReadResult:
    text: str
    source_url: str
    backend: str
    ok: bool


async def read_url(url: str, settings: Settings, mode: str = "auto") -> UrlReadResult:
    url = str(url or "").strip()
    mode = str(mode or "auto").strip().lower()
    if mode not in ("auto", "x_thread"):
        return UrlReadResult(f"Mode fetch_url không hợp lệ: {mode}", url, "invalid", False)

    reason = reader.validate_public_url(url)
    if reason:
        return UrlReadResult(f"Không thể tải trang: {reason}", url, "blocked", False)

    target = x_reader.parse_x_status_url(url)
    if mode == "x_thread" and target is None:
        return UrlReadResult(
            "Mode x_thread chỉ áp dụng cho URL X/Twitter status.",
            url,
            "invalid",
            False,
        )

    if target is not None and settings.x_fetch_enabled:
        specialized = await x_reader.read_x_url(
            target,
            mode,
            timeout=settings.request_timeout_sec,
        )
        if specialized is not None:
            return UrlReadResult(
                specialized.text,
                specialized.canonical_url,
                specialized.backend,
                True,
            )

    fetch_url = target.canonical_url if target is not None else url
    if (
        settings.crawl4ai_enabled
        and settings.crawl4ai_url.strip()
        and settings.crawl4ai_api_token.strip()
    ):
        try:
            crawl = await crawl4ai_client.read_page(fetch_url, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - this boundary must degrade to generic reading
            logger.warning(
                "Crawl4AI URL read failed (unexpected_client_error); fallback generic reader"
            )
        else:
            if crawl.ok:
                return UrlReadResult(crawl.text, crawl.source_url, "crawl4ai", True)
            logger.warning(
                "Crawl4AI URL read failed (%s); fallback generic reader",
                crawl.reason,
            )

    text = await reader.read_page(fetch_url, timeout=settings.request_timeout_sec)
    ok = bool(text) and not text.startswith("Không tải được trang")
    return UrlReadResult(text, fetch_url, "generic_reader", ok)
