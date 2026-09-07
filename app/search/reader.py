"""Mạng + chống SSRF khi đọc trang web.

Chống SSRF 3 lớp, trong đó lớp "IP pinning" (resolve trước -> chỉ kết nối tới IP
công khai đã kiểm tra, không dùng DNS lần 2 của thư viện) đóng kẽ hở
DNS-rebinding/TOCTOU: dù DNS đổi sang IP nội bộ ở lần resolve thứ 2 (lúc httpx
kết nối thật), bước kết nối vẫn bị chặn.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpcore
import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
MAX_CHARS = 8000  # số ký tự văn bản trả về cho model
MAX_BYTES = 2 * 1024 * 1024  # dung lượng tối đa tải về (tránh tải file khổng lồ)
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_RE_NEWLINES = re.compile(r"\n{3,}")


class SSRFBlocked(Exception):
    """Kết nối bị chặn vì không phải IP công khai."""


def _is_public_ip(ip_str: str) -> bool:
    """IP literal có phải địa chỉ công khai (is_global) không?"""
    try:
        return ipaddress.ip_address(ip_str.split("%")[0]).is_global
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# IP pinning: resolve host -> chỉ giữ IP công khai; kết nối thẳng tới IP đó
# ---------------------------------------------------------------------------
async def _resolve_all(host: str) -> list[str]:
    """Resolve host -> danh sách IP CHỈ công khai.

    Trả về rỗng nếu không có IP công khai nào (kể cả DNS lỗi) — fail-closed:
    không tự resolve được IP public thì không kết nối bằng DNS lần 2.
    """

    def _res() -> list[str]:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for info in infos:
            key = info[4][0].split("%")[0]
            if key in seen:
                continue
            seen.add(key)
            if _is_public_ip(key):
                out.append(key)
        return out

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _res)


class SSRFCheckBackend(httpcore.AsyncNetworkBackend):
    """Bọc backend mạng: mọi kết nối TCP đều tới IP công khai đã pin.

    - Host là IP literal: kiểm tra ``is_global`` rồi ủy quyền backend thật
      (``httpcore.AnyIOBackend``) — KHÔNG dùng lớp abstract ``AsyncNetworkBackend``.
    - Host là hostname: tự resolve trước (chỉ giữ IP public, cache ngắn), rồi
      ``connect_tcp`` thẳng tới IP đã pin. httpcore vẫn gọi ``start_tls`` với
      ``server_hostname`` = hostname gốc (SNI + xác thực chứng chỉ đúng).
    """

    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._backend = backend or httpcore.AnyIOBackend()
        self._pin_ttl = 300.0
        self._pinned: dict[str, tuple[float, list[str]]] = {}
        self._pin_lock = asyncio.Lock()

    async def _resolve_pinned(self, host: str) -> list[str]:
        now = time.monotonic()
        hit = self._pinned.get(host)
        if hit and now - hit[0] < self._pin_ttl:
            return hit[1]
        async with self._pin_lock:
            hit = self._pinned.get(host)
            if hit and time.monotonic() - hit[0] < self._pin_ttl:
                return hit[1]
            ips = await _resolve_all(host)
            self._pinned[host] = (time.monotonic(), ips)
            return ips

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        host_clean = str(host).lower().rstrip(".")
        try:
            ip = ipaddress.ip_address(host_clean)
        except ValueError:
            ip = None
        if ip is not None:
            if not ip.is_global:
                raise SSRFBlocked("địa chỉ IP không công khai — chặn kết nối")
            # Dùng dạng chuẩn (bỏ trailing-dot) để backend không hiểu nhầm thành hostname.
            return await self._backend.connect_tcp(
                str(ip),
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

        ips = await self._resolve_pinned(str(host))
        if not ips:
            raise SSRFBlocked(f"host '{host}' không resolve ra IP công khai nào — chặn kết nối")
        last_exc: BaseException | None = None
        for ip_str in ips:
            try:
                return await self._backend.connect_tcp(
                    ip_str,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except SSRFBlocked:
                raise
            except Exception as exc:
                last_exc = exc
                continue
        if last_exc is not None:
            raise last_exc
        raise SSRFBlocked(f"kết nối tới {host} thất bại (mọi IP đều lỗi)")

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise SSRFBlocked("unix socket không được phép")

    async def sleep(self, seconds: float) -> None:
        return await self._backend.sleep(seconds)


# ---------------------------------------------------------------------------
# Validate URL + đọc trang
# ---------------------------------------------------------------------------
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


class _FetchError(Exception):
    """Lỗi khi tải (kèm lý do tiếng Việt, hiển thị được cho model)."""


@dataclass
class _FetchResult:
    body: bytes


async def _fetch_limited(
    client: httpx.AsyncClient, url: str, max_bytes: int = MAX_BYTES
) -> _FetchResult:
    """GET url (kiểm tra an toàn ở MỌI bước chuyển hướng), giới hạn dung lượng."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        reason = validate_public_url(current)
        if reason:
            raise _FetchError(reason)
        async with client.stream("GET", current, headers={"User-Agent": _UA}) as resp:
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
            return _FetchResult(body=b"".join(chunks))
    raise _FetchError("quá nhiều lần chuyển hướng")


def _build_client(timeout: float) -> httpx.AsyncClient:
    """Client có transport chống SSRF (IP pinning) — KHÔNG follow redirect tự động
    (mỗi chặng redirect đều được validate lại trong _fetch_limited)."""
    backend = SSRFCheckBackend(httpcore.AnyIOBackend())
    pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        http1=True,
        http2=False,
        network_backend=backend,
    )
    transport = httpx.AsyncHTTPTransport()
    transport._pool = pool  # noqa: SLF001 — lắp pool tuỳ biến (network_backend)
    return httpx.AsyncClient(transport=transport, timeout=timeout, follow_redirects=False)


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
    http = client or _build_client(timeout)
    try:
        # 1) Jina Reader — không cần key, trả text sạch (Jina tự tải hộ nên an toàn)
        try:
            result = await _fetch_limited(http, f"https://r.jina.ai/{url}")
            text = result.body.decode("utf-8", errors="replace").strip()
            if text:
                return text[:MAX_CHARS]
        except (_FetchError, httpx.HTTPError, SSRFBlocked) as exc:
            logger.debug("Jina Reader lỗi (chuyển fallback): %s", exc)

        # 2) Fallback: tải HTML trực tiếp và parse văn bản
        try:
            result = await _fetch_limited(http, url)
        except (_FetchError, httpx.HTTPError, SSRFBlocked) as exc:
            return f"Không tải được trang: {exc}"
        except Exception as exc:  # noqa: BLE001 — không để lỗi mạng lạ sập tool
            return f"Không tải được trang: {exc}"
        body = result.body
        soup = BeautifulSoup(body, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        text = _RE_NEWLINES.sub("\n\n", text)
        return text[:MAX_CHARS]
    finally:
        if own_client:
            await http.aclose()
