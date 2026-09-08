"""Hàm định dạng văn bản & trợ giúp message."""

from __future__ import annotations

import html
import ipaddress
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

_WHITESPACE_RE = re.compile(r"\s+")
_TRACKING_QUERY_KEYS = frozenset(
    {
        "_ga",
        "_gl",
        "dclid",
        "fbclid",
        "gbraid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "twclid",
        "wbraid",
        "yclid",
    }
)
_TLD_EXTRACT = tldextract.TLDExtract(
    suffix_list_urls=(),
    include_psl_private_domains=True,
)


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


def _normalized_host(host: str) -> tuple[str, bool]:
    """Chuẩn hoá hostname cho so sánh; trả thêm cờ IPv6 để dựng lại URL."""
    host = host.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            return host.encode("idna").decode("ascii"), False
        except UnicodeError:
            return "", False
    return address.compressed, address.version == 6


def _is_tracking_query_key(key: str) -> bool:
    key = key.lower()
    return key.startswith("utm_") or key in _TRACKING_QUERY_KEYS


def canonicalize_source_url(value: object) -> str:
    """Chuẩn hoá URL nguồn bằng quy tắc generic, không phụ thuộc website cụ thể."""
    raw_url = str(value or "").strip()
    if not raw_url:
        return ""
    try:
        parsed = urlsplit(raw_url)
        scheme = parsed.scheme.lower()
        host, is_ipv6 = _normalized_host(parsed.hostname or "")
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if scheme not in {"http", "https"} or not host:
        return ""

    netloc = f"[{host}]" if is_ipv6 else host
    is_default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    if port is not None and not is_default_port:
        netloc = f"{netloc}:{port}"

    path = parsed.path.rstrip("/")
    query_pairs = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_query_key(key)
    ]
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def source_family_key(url: str) -> str:
    """Lấy registrable domain/eTLD+1 để nhóm nguồn cho mọi website."""
    canonical = canonicalize_source_url(url)
    if not canonical:
        return ""
    try:
        host = urlsplit(canonical).hostname or ""
    except ValueError:
        return ""
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        extracted = _TLD_EXTRACT(host)
        return extracted.top_domain_under_public_suffix or host
    return host


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
    chữ xanh để bấm, ẩn path/query dài. Chỉ giữ một URL cho mỗi registrable domain
    để một website không chiếm hết danh sách tham khảo; private PSL suffixes được
    tôn trọng để các tenant độc lập (vd. *.github.io) không bị gộp nhầm.
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
