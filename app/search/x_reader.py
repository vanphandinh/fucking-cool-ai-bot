"""Specialized free-first reader for public X/Twitter status URLs."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

_X_HOSTS = frozenset(
    {
        "x.com",
        "www.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
        "m.twitter.com",
        "fxtwitter.com",
        "www.fxtwitter.com",
        "fixupx.com",
        "www.fixupx.com",
    }
)
_STATUS_ID_RE = re.compile(r"^\d{2,20}$")
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
MAX_X_TEXT = 5500
MAX_THREAD_POSTS = 12


@dataclass(frozen=True)
class XStatusTarget:
    status_id: str
    canonical_url: str


@dataclass(frozen=True)
class XReadResult:
    text: str
    backend: str
    canonical_url: str
    thread_complete: bool = False


class XFetchError(Exception):
    """A specialized X upstream returned unusable data."""


def parse_x_status_url(url: str) -> XStatusTarget | None:
    """Recognize supported X/Twitter/mirror status URLs and return a canonical X URL."""
    if not isinstance(url, str) or not url or "%" in url:
        return None
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in ("http", "https") or host not in _X_HOSTS:
        return None
    segments = [segment for segment in parts.path.split("/") if segment]
    try:
        status_index = segments.index("status")
    except ValueError:
        return None
    if status_index == 0 or status_index + 1 >= len(segments):
        return None
    status_id = segments[status_index + 1]
    if not _STATUS_ID_RE.fullmatch(status_id):
        return None
    handle = segments[status_index - 1]
    if handle == "i":
        canonical = f"https://x.com/i/status/{status_id}"
    elif _HANDLE_RE.fullmatch(handle):
        canonical = f"https://x.com/{handle}/status/{status_id}"
    else:
        return None
    return XStatusTarget(status_id=status_id, canonical_url=canonical)


def _num(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _media_descriptions(status: dict) -> list[str]:
    media = status.get("media") if isinstance(status.get("media"), dict) else {}
    items: list[dict] = []
    for key in ("all", "photos", "videos", "gifs"):
        value = media.get(key)
        if isinstance(value, list):
            items.extend(item for item in value if isinstance(item, dict))
    descriptions: list[str] = []
    for item in items[:6]:
        kind = str(item.get("type") or item.get("kind") or "media")[:30]
        alt = str(item.get("altText") or item.get("alt_text") or "").strip()[:300]
        desc = f"{kind}: {alt}" if alt else kind
        if desc not in descriptions:
            descriptions.append(desc)
    return descriptions


def _format_status(status: dict) -> str:
    author = status.get("author") if isinstance(status.get("author"), dict) else {}
    name = str(author.get("name") or "Unknown")[:120]
    handle = str(author.get("screen_name") or "")[:30]
    who = f"{name} (@{handle})" if handle else name
    lines = [f"Author: {who}"]
    if created := str(status.get("created_at") or "").strip():
        lines.append(f"Created: {created[:80]}")
    body = str(status.get("text") or "").strip()[:4000]
    lines.extend(["Text:", body])

    metrics: list[str] = []
    for label, key in (
        ("likes", "likes"),
        ("reposts", "reposts"),
        ("quotes", "quotes"),
        ("replies", "replies"),
        ("views", "views"),
    ):
        value = _num(status.get(key))
        if value is not None:
            metrics.append(f"{label}={value}")
    if metrics:
        lines.append("Metrics: " + ", ".join(metrics))

    media = _media_descriptions(status)
    if media:
        lines.append("Media: " + "; ".join(media))

    quote = status.get("quote")
    if isinstance(quote, dict) and quote.get("type") != "tombstone" and quote.get("text"):
        lines.extend(["Quoted post:", _format_status(quote)[:1500]])
    return "\n".join(part for part in lines if part).strip()[:MAX_X_TEXT]


def _format_thread(data: dict) -> str:
    focal = data.get("status") if isinstance(data.get("status"), dict) else None
    raw_thread = data.get("thread") if isinstance(data.get("thread"), list) else []
    ordered: list[dict] = []
    seen: set[str] = set()
    candidates = ([focal] if focal else []) + raw_thread
    for item in candidates:
        if not isinstance(item, dict) or item.get("type") == "tombstone":
            continue
        status_id = str(item.get("id") or "")
        if status_id and status_id in seen:
            continue
        if status_id:
            seen.add(status_id)
        ordered.append(item)
    truncated = len(ordered) > MAX_THREAD_POSTS
    ordered = ordered[:MAX_THREAD_POSTS]
    blocks = [
        f"Post {index}:\n{_format_status(item)[:700]}"
        for index, item in enumerate(ordered, start=1)
    ]
    if truncated:
        blocks.append(f"Thread truncated to {MAX_THREAD_POSTS} posts.")
    return "\n\n".join(blocks)[:MAX_X_TEXT]


async def _fetch_fxtwitter(
    target: XStatusTarget,
    mode: str,
    client: httpx.AsyncClient,
) -> XReadResult:
    endpoint = "thread" if mode == "x_thread" else "status"
    response = await client.get(
        f"https://api.fxtwitter.com/2/{endpoint}/{target.status_id}",
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise XFetchError("FxTwitter returned non-JSON data") from exc
    if not isinstance(data, dict):
        raise XFetchError("FxTwitter returned invalid JSON")
    raw_code = data.get("code")
    try:
        code = int(raw_code or 0)
    except (TypeError, ValueError) as exc:
        code_type = type(raw_code).__name__
        raise XFetchError(f"FxTwitter API returned invalid code type: {code_type}") from exc
    if code != 200:
        raise XFetchError(f"FxTwitter API code={raw_code}")
    if mode == "x_thread":
        text = _format_thread(data)
        if not text:
            raise XFetchError("FxTwitter thread is empty")
        return XReadResult(text, "fxtwitter_thread", target.canonical_url, True)
    status = data.get("status")
    if not isinstance(status, dict):
        raise XFetchError("FxTwitter status is missing")
    text = _format_status(status)
    if not text:
        raise XFetchError("FxTwitter status is empty")
    return XReadResult(text, "fxtwitter_status", target.canonical_url, False)


async def _fetch_oembed(
    target: XStatusTarget,
    client: httpx.AsyncClient,
) -> XReadResult:
    response = await client.get(
        "https://publish.x.com/oembed",
        params={"url": target.canonical_url, "omit_script": "1", "dnt": "true"},
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise XFetchError("X oEmbed returned non-JSON data") from exc
    if not isinstance(data, dict):
        raise XFetchError("X oEmbed returned invalid JSON")
    soup = BeautifulSoup(str(data.get("html") or ""), "html.parser")
    paragraph = soup.find("p")
    body = paragraph.get_text(" ", strip=True) if paragraph else ""
    if not body:
        raise XFetchError("X oEmbed has no post text")
    author = str(data.get("author_name") or "").strip()[:120]
    text = f"Author: {author}\nText:\n{body}" if author else f"Text:\n{body}"
    return XReadResult(text[:MAX_X_TEXT], "x_oembed", target.canonical_url, False)


async def read_x_url(
    target: XStatusTarget,
    mode: str,
    timeout: float,
    client: httpx.AsyncClient | None = None,
) -> XReadResult | None:
    """Resolve an X status via free structured sources within one total deadline."""
    if mode not in ("auto", "x_thread"):
        return None
    deadline = max(1.0, min(float(timeout), 12.0))
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=deadline, follow_redirects=False)
    try:
        try:
            async with asyncio.timeout(deadline):
                if mode == "x_thread":
                    try:
                        return await _fetch_fxtwitter(target, "x_thread", http)
                    except (XFetchError, httpx.HTTPError, ValueError):
                        try:
                            focal = await _fetch_fxtwitter(target, "auto", http)
                            return XReadResult(
                                "Full thread unavailable; focal post only.\n" + focal.text,
                                focal.backend,
                                focal.canonical_url,
                                False,
                            )
                        except (XFetchError, httpx.HTTPError, ValueError):
                            pass
                else:
                    try:
                        return await _fetch_fxtwitter(target, "auto", http)
                    except (XFetchError, httpx.HTTPError, ValueError):
                        pass
                try:
                    return await _fetch_oembed(target, http)
                except (XFetchError, httpx.HTTPError, ValueError):
                    return None
        except TimeoutError:
            return None
    finally:
        if own_client:
            await http.aclose()
