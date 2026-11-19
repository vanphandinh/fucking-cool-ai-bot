"""Hàm định dạng văn bản & trợ giúp message."""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from .source_policy import select_diverse_sources

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
