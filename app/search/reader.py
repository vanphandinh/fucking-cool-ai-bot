"""Đọc nội dung trang web (Jina Reader trước, fallback tự parse HTML).

An toàn: mọi URL (kể cả URL sau chuyển hướng) đều phải qua validate_public_url
(chống SSRF: IP literal nội bộ, dạng viết tắt, IPv4-mapped, zone index...),
hostname phải resolve ra ít nhất 1 IP public (chống DNS-rebinding qua nip.io/sslip.io),
và nội dung tải về bị giới hạn dung lượng để tránh lạm dụng băng thông.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
MAX_CHARS = 8000          # số ký tự văn bản trả về cho model
MAX_BYTES = 2 * 1024 * 1024  # dung lượng tối đa tải về (tránh tải file khổng lồ)
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_RE_NEWLINES = re.compile(r"\n{3,}")

# Cache kết quả "hostname có resolve ra IP public không" (host hiếm khi đổi IP)
_RESOLVE_TTL = 600.0
_resolve_cache: dict[str, tuple[float, bool]] = {}


class _FetchError(Exception):
    """Lỗi khi tải (kèm lý do tiếng Việt, hiển thị được cho model)."""


def _is_numeric_host(host: str) -> bool:
    """Host có dạng thuần số theo luật 'numbers-and-dots' của glibc không?

    Ví dụ: ``127.1``, ``2130706433``, ``0x7f.1``, ``0177.0.0.1`` — những dạng này
    ``ipaddress`` không parse được nhưng trình phân giải vẫn hiểu thành IP.
    """
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return False
    for part in parts:
        if not part:
            return False
        low = part.lower()
        # Mỗi phần phải bắt đầu bằng chữ số (hoặc 0x cho hệ hex) mới đáng nghi
        if not (part[0].isdigit() or low.startswith("0x")):
            return False
    return True


def _inet_aton_to_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Mô phỏng luật parse IP viết tắt của glibc ``inet_aton``.

    Trả về địa chỉ IPv4 tương đương, hoặc None nếu host không phải (hoặc không
    parse được thành) IP dạng số. Đây là lớp chống SSRF thứ 2 sau ipaddress:
    chặn ``127.1`` -> 127.0.0.1, ``2130706433`` -> 127.0.0.1, v.v.
    """
    if not _is_numeric_host(host):
        return None
    nums: list[int] = []
    for part in host.split("."):
        low = part.lower()
        try:
            if low.startswith("0x"):
                nums.append(int(part, 16))
            elif len(part) > 1 and part.startswith("0"):
                nums.append(int(part, 8))  # số octal — "08" sẽ ValueError như glibc
            else:
                nums.append(int(part, 10))
        except ValueError:
            return None

    n: int | None = None
    if len(nums) == 1 and nums[0] <= 0xFFFFFFFF:
        n = nums[0]
    elif len(nums) == 2 and nums[0] <= 0xFF and nums[1] <= 0xFFFFFF:
        n = (nums[0] << 24) | nums[1]
    elif len(nums) == 3 and nums[0] <= 0xFF and nums[1] <= 0xFF and nums[2] <= 0xFFFF:
        n = (nums[0] << 24) | (nums[1] << 16) | nums[2]
    elif len(nums) == 4 and all(v <= 0xFF for v in nums):
        n = (nums[0] << 24) | (nums[1] << 16) | (nums[2] << 8) | nums[3]
    if n is None:
        return None
    return ipaddress.IPv4Address(n)


def validate_public_url(url: str) -> str | None:
    """Kiểm tra URL có an toàn để bot tải không (chống SSRF).

    Trả về None nếu hợp lệ, ngược lại trả về lý do từ chối (tiếng Việt).
    Chặn: scheme khác http/https, URL có user:pass, host rỗng, localhost,
    mọi địa chỉ IP không public (private/loopback/link-local/reserved/multicast/
    CGNAT...) kể cả dạng viết tắt (127.1, 0x7f000001...) và IPv4-mapped IPv6.
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

    # Ký tự % (zone index IPv6 như [fe80::1%25eth0]) không bao giờ hợp lệ trong
    # host công khai và ipaddress không parse được -> từ chối trước.
    if "%" in host:
        return "Không cho phép địa chỉ IPv6 có zone index hoặc host chứa ký tự lạ."

    ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if ":" in host:
            # IPv6 dạng rác không parse được -> không thể xác minh, từ chối an toàn
            return "Địa chỉ IPv6 không hợp lệ."
        # Dạng IP viết tắt kiểu "numbers-and-dots" (127.1, 2130706433, ...)
        if _is_numeric_host(host):
            ip = _inet_aton_to_ipv4(host)
            if ip is None:
                return "Địa chỉ IP dạng số không hợp lệ."
    if ip is not None and not ip.is_global:
        return "Không cho phép tải địa chỉ IP nội bộ."
    return None


async def _host_resolves_public(host: str) -> bool:
    """Hostname có resolve ra ít nhất 1 IP public không? (chống DNS-rebinding)

    Trả về True nếu: resolve thành công và có IP global; HOẶC không resolve được
    (mạng lỗi/offline — lúc đó chính bước kết nối sẽ tự fail, không mở lỗ hổng).
    Chỉ trả về False khi resolve THÀNH CÔNG mà toàn bộ IP đều không public.
    """
    now = time.monotonic()
    cached = _resolve_cache.get(host)
    if cached and now - cached[0] < _RESOLVE_TTL:
        return cached[1]

    def _resolve() -> bool:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return True  # không resolve được (NXDOMAIN/mất mạng) -> để httpx tự xử lý
        ips = {info[4][0] for info in infos}
        if not ips:
            return True
        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str.split("%")[0])
            except ValueError:
                continue
            if ip.is_global:
                return True
        return False  # resolve được nhưng toàn bộ đều private/loopback/...

    try:
        ok = await asyncio.wait_for(asyncio.to_thread(_resolve), timeout=3.0)
    except asyncio.TimeoutError:
        ok = True  # DNS chậm bất thường — bước kết nối thực tế sẽ tự fail nếu không tới được
    _resolve_cache[host] = (now, ok)
    return ok


async def _fetch_limited(
    client: httpx.AsyncClient, url: str, max_bytes: int = MAX_BYTES
) -> bytes:
    """GET url (kiểm tra an toàn ở MỌI bước chuyển hướng), giới hạn dung lượng."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        reason = validate_public_url(current)
        if reason:
            raise _FetchError(reason)
        host = (urlparse(current).hostname or "").lower()
        if host and not await _host_resolves_public(host):
            raise _FetchError(
                "trang resolve về địa chỉ không public (nghi chuyển hướng SSRF)"
            )
        async with client.stream(
            "GET", current, headers={"User-Agent": _UA}
        ) as resp:
            if resp.status_code in _REDIRECT_STATUSES:
                location = resp.headers.get("location")
                if not location:
                    raise _FetchError("trang chuyển hướng thiếu địa chỉ đích")
                current = urljoin(current, location)
                continue  # vòng lặp sẽ validate URL đích trước khi tải
            if resp.status_code >= 400:
                raise _FetchError(f"HTTP {resp.status_code}")
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise _FetchError(f"trang quá lớn (> {max_bytes // (1024 * 1024)}MB)")
                chunks.append(chunk)
            return b"".join(chunks)
    raise _FetchError("quá nhiều lần chuyển hướng")


async def read_page(
    url: str, timeout: float = 30.0, client: httpx.AsyncClient | None = None
) -> str:
    """Trả về văn bản rút gọn của trang (<= MAX_CHARS) hoặc lý do không tải được.

    ``client`` chỉ dành cho kiểm thử (chèn transport giả); bình thường để None.
    """
    reason = validate_public_url(url)
    if reason:
        return f"Không tải được trang: {reason}"

    own_client = client is None
    http = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        # 1) Jina Reader — không cần key, trả text sạch (Jina tự tải hộ nên an toàn)
        try:
            body = await _fetch_limited(http, f"https://r.jina.ai/{url}")
            text = body.decode("utf-8", errors="replace").strip()
            if text:
                return text[:MAX_CHARS]
        except (_FetchError, httpx.HTTPError) as exc:
            logger.debug("Jina Reader lỗi (chuyển fallback): %s", exc)

        # 2) Fallback: tải HTML trực tiếp và parse văn bản
        try:
            body = await _fetch_limited(http, url)
        except (_FetchError, httpx.HTTPError) as exc:
            return f"Không tải được trang: {exc}"
        soup = BeautifulSoup(body, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        text = _RE_NEWLINES.sub("\n\n", text)
        return text[:MAX_CHARS]
    finally:
        if own_client:
            await http.aclose()
