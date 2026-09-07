"""Đọc nội dung trang web (Jina Reader trước, fallback tự parse HTML)."""
from __future__ import annotations

import ipaddress
import logging
import re
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
MAX_CHARS = 8000
_RE_NEWLINES = re.compile(r"\n{3,}")


def validate_public_url(url: str) -> str | None:
    """Kiểm tra URL có an toàn để bot tải không (chống SSRF).

    Trả về None nếu hợp lệ, ngược lại trả về lý do từ chối (tiếng Việt).
    Chặn: scheme khác http/https, URL có user:pass, host rỗng, localhost,
    địa chỉ IP private/loopback/link-local/reserved/multicast.
    """
    try:
        parts = urlparse(url)
    except ValueError:
        return "URL không hợp lệ."
    if parts.scheme not in ("http", "https"):
        return "Chỉ cho phép URL http/https."
    if parts.username or parts.password:
        return "URL không được chứa thông tin đăng nhập."
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return "URL thiếu tên miền."
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        return "Không cho phép tải địa chỉ nội bộ (localhost/.local)."
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast
    ):
        return "Không cho phép tải địa chỉ IP nội bộ."
    return None


async def read_page(url: str, timeout: float = 30.0) -> str:
    """Trả về văn bản rút gọn của trang (<= MAX_CHARS)."""
    # 1) Jina Reader — không cần key (~20 RPM), trả text sạch
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            resp = await client.get(f"https://r.jina.ai/{url}", headers={"User-Agent": _UA})
            if resp.status_code == 200 and resp.text.strip():
                return resp.text[:MAX_CHARS]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Jina Reader lỗi: %s", exc)

        # 2) Fallback: tải HTML và parse văn bản
        resp = await client.get(url, headers={"User-Agent": _UA})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        text = _RE_NEWLINES.sub("\n\n", text)
        return text[:MAX_CHARS]
