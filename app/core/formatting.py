"""Hàm định dạng văn bản & trợ giúp message."""
from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"\s+")


def clean_question(text: str, username: str) -> str:
    """Bỏ mention @username khỏi câu hỏi, dồn khoảng trắng, trim.

    Vd: "giá vàng @bot?" -> "giá vàng" (dấu câu thừa quanh mention bị bỏ).
    """
    username = username.lower().lstrip("@")
    result = re.sub(rf"@\b{re.escape(username)}\b", " ", text, flags=re.IGNORECASE)
    result = _WHITESPACE_RE.sub(" ", result).strip()
    result = result.strip(" \t,;:!?.-…")
    return result


def split_plain(text: str, limit: int = 3900) -> list[str]:
    """Cắt text dài (> giới hạn Telegram 4096) thành nhiều phần an toàn."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = _find_cut(rest, limit)
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
        if not rest:
            break
    if rest:
        parts.append(rest)
    return parts or [text]


def _find_cut(text: str, limit: int) -> int:
    """Tìm vị trí cắt gần limit nhất, ưu tiên ranh giới đoạn/câu."""
    delimiters = ["\n\n", "\n", ". ", "? ", "! "]
    best = limit
    for delim in delimiters:
        pos = text.rfind(delim, 0, limit)
        if pos != -1 and limit - pos <= 600:
            best = pos + len(delim)
            break
    return min(best, limit)


def format_sources(sources: list[dict[str, str]]) -> str:
    """Danh sách nguồn dạng văn bản thường (URL được Telegram tự link)."""
    lines = ["📚 Nguồn tham khảo:"]
    seen: set[str] = set()
    n = 0
    for src in sources:
        url = (src.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        n += 1
        title = (src.get("title") or url).strip()
        if len(title) > 90:
            title = title[:87] + "..."
        lines.append(f"{n}. {title}\n   {url}")
        if n >= 6:
            break
    return "\n".join(lines) if n else ""
