"""Hàm định dạng văn bản & trợ giúp message."""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

_WHITESPACE_RE = re.compile(r"\s+")
_X_SOURCE_HOSTS = frozenset(
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
_GITHUB_SOURCE_HOSTS = frozenset(
    {"github.com", "www.github.com", "gist.github.com", "www.gist.github.com"}
)
_X_STATUS_ID_RE = re.compile(r"^\d{2,20}$")
_X_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


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
    """Chuẩn hoá URL nguồn để các biến thể cùng nội dung không bị tính riêng."""
    url = str(value or "").strip()
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        return ""

    if host in _X_SOURCE_HOSTS:
        segments = [segment for segment in parsed.path.split("/") if segment]
        try:
            status_index = segments.index("status")
        except ValueError:
            status_index = -1
        if status_index > 0 and status_index + 1 < len(segments):
            handle = segments[status_index - 1]
            status_id = segments[status_index + 1]
            if _X_STATUS_ID_RE.fullmatch(status_id):
                if handle == "i":
                    return f"https://x.com/i/status/{status_id}"
                if _X_HANDLE_RE.fullmatch(handle):
                    return f"https://x.com/{handle}/status/{status_id}"
    return url


def source_family_key(url: str) -> str:
    """Nhóm các URL cùng website/platform để footer ưu tiên nguồn đa dạng."""
    canonical = canonicalize_source_url(url)
    if not canonical:
        return ""
    try:
        host = (urlparse(canonical).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    if host in _X_SOURCE_HOSTS or host == "x.com":
        return "x.com"
    if host in _GITHUB_SOURCE_HOSTS:
        return "github.com"
    if host.startswith("www."):
        host = host[4:]
    return host


def _hostname(url: str) -> str:
    """Lấy hostname ngắn (bỏ www.) để làm nhãn khi không có tiêu đề."""
    try:
        host = (urlparse(url).hostname or "").lower()
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
    chữ xanh để bấm, ẩn path/query dài. Chỉ giữ một URL cho mỗi source family để
    tránh nhiều link cùng website/platform chiếm hết danh sách tham khảo.
    """
    lines = ["📚 Nguồn tham khảo:"]
    seen_urls: set[str] = set()
    seen_families: set[str] = set()
    n = 0
    for src in sources:
        if not isinstance(src, dict):
            continue
        url = canonicalize_source_url(src.get("url"))
        family = source_family_key(url)
        if not url or not family or url in seen_urls or family in seen_families:
            continue
        label = _source_label(str(src.get("title") or ""), url)
        safe_label = html.escape(label, quote=False)
        safe_url = html.escape(url, quote=True)
        line = f'{n + 1}. <a href="{safe_url}">{safe_label}</a>'
        # URL do search backend cung cấp có thể cực dài. Không để riêng footer
        # vượt giới hạn 4096 UTF-16 của Telegram và làm mất toàn bộ danh sách nguồn.
        if _utf16_len("\n".join([*lines, line])) > 3900:
            continue
        seen_urls.add(url)
        seen_families.add(family)
        lines.append(line)
        n += 1
        if n >= 6:
            break
    return "\n".join(lines) if n else ""
