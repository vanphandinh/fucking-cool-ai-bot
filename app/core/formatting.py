"""Hàm định dạng văn bản & trợ giúp message."""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from .source_policy import (
    canonicalize_source_url as _canonicalize_source_url,
    select_diverse_sources,
    source_site_key,
)

_WHITESPACE_RE = re.compile(r"\s+")


def clean_question(text: str, username: str) -> str:
    """Bỏ mention @username khỏi câu hỏi, dồn khoảng trắng, trim.

    Vd: "giá vàng @bot?" -> "giá vàng" (dấu câu thừa quanh mention bị bỏ).
    """
    username = username.lower().lstrip("@")
    result = re.sub(rf"@\b{re.escape(username)}\b", " ", text, flags=re.IGNORECASE)
    result = _WHITESPACE_RE.sub(" ", result).strip()
    # Bỏ dấu câu phân cách ở hai đầu. KHÔNG bỏ '-' (số âm, cờ lệnh).
    result = result.strip(" \t,;:?!….")
    return result


def _utf16_len(text: str) -> int:
    """Độ dài theo UTF-16 code unit — Telegram đếm 4096 theo đơn vị này, không phải len()."""
    return len(text.encode("utf-16-le")) // 2


def _prefix_fitting(text: str, limit: int) -> int:
    """Chỉ số Python lớn nhất i sao cho utf16_len(text[:i]) <= limit."""
    if limit <= 0:
        return 0
    if _utf16_len(text) <= limit:
        return len(text)
    raw = text.encode("utf-16-le")
    byte_limit = limit * 2
    chunk = raw[:byte_limit]
    if len(chunk) >= 2:
        last = int.from_bytes(chunk[-2:], "little")
        if 0xD800 <= last <= 0xDBFF:  # high surrogate lẻ — không cắt giữa cặp
            chunk = chunk[:-2]
    return len(chunk.decode("utf-16-le"))


def split_plain(text: str, limit: int = 3900) -> list[str]:
    """Cắt text dài (> giới hạn Telegram 4096 UTF-16) thành nhiều phần an toàn."""
    text = text.strip()
    if not text:
        return []
    if _utf16_len(text) <= limit:
        return [text]

    parts: list[str] = []
    rest = text
    while _utf16_len(rest) > limit:
        cut = _find_cut(rest, limit)
        if cut <= 0:
            cut = max(_prefix_fitting(rest, limit), 1)
        chunk = rest[:cut].strip()
        if chunk:
            parts.append(chunk)
        rest = rest[cut:].strip()
        if not rest:
            break
    if rest:
        parts.append(rest)
    return parts or [text]


def _find_cut(text: str, limit: int) -> int:
    """Tìm vị trí cắt gần limit nhất (theo UTF-16), ưu tiên ranh giới đoạn/câu."""
    max_i = _prefix_fitting(text, limit)
    if max_i <= 0:
        return 0
    delimiters = ["\n\n", "\n", ". ", "? ", "! "]
    for delim in delimiters:
        pos = text.rfind(delim, 0, max_i)
        if pos != -1 and max_i - pos <= 600:
            return pos + len(delim)
    return max_i


def canonicalize_source_url(value: object) -> str:
    """Backward-compatible wrapper for generic source URL canonicalization."""
    return _canonicalize_source_url(value)


def source_family_key(url: str) -> str:
    """Backward-compatible alias for the registrable source-site key."""
    return source_site_key(url)


def _hostname(url: str) -> str:
    """Lấy hostname ngắn (bỏ www.) để làm nhãn khi không có tiêu đề."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except Exception:  # noqa: BLE001 — URL lạ thì thôi, không nổ
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _source_label(title: str, url: str) -> str:
    """Nhãn ngắn để bấm: ưu tiên tiêu đề trang, không dùng URL dài."""
    title = _WHITESPACE_RE.sub(" ", (title or "").strip())
    if title and title != url and not title.lower().startswith(("http://", "https://")):
        if len(title) > 90:
            title = title[:87] + "..."
        return title
    return _hostname(url) or "Nguồn"


def format_sources(sources: list[dict[str, str]]) -> str:
    """Danh sách nguồn HTML: tiêu đề ngắn bấm được, không in URL dài.

    Gửi kèm parse_mode=HTML. Mỗi mục là <a href="...">nhãn</a> — Telegram hiện
    chữ xanh để bấm, ẩn path/query dài. Source selection dùng chung policy với
    orchestrator; formatter vẫn quét toàn bộ nguồn hợp lệ để URL quá dài không
    chiếm mất slot của nguồn phía sau.
    """
    lines = ["📚 Nguồn tham khảo:"]
    n = 0
    selected = select_diverse_sources(sources, limit=len(sources))
    for src in selected:
        url = src["url"]
        label = _source_label(src["title"], url)
        safe_label = html.escape(label, quote=False)
        safe_url = html.escape(url, quote=True)
        line = f'{n + 1}. <a href="{safe_url}">{safe_label}</a>'
        # URL do search backend cung cấp có thể cực dài. Không để riêng footer
        # vượt giới hạn 4096 UTF-16 của Telegram và làm mất toàn bộ danh sách nguồn.
        if _utf16_len("\n".join([*lines, line])) > 3900:
            continue
        lines.append(line)
        n += 1
        if n >= 6:
            break
    return "\n".join(lines) if n else ""
