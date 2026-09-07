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
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

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
# Translation/tunnelling can hide an internal IPv4 destination. These address
# mechanisms are not supported by the public-page reader; fail closed.
_IPV6_TRANSITION_NETWORKS = tuple(
    ipaddress.ip_network(prefix)
    for prefix in (
        "64:ff9b::/96",
        "64:ff9b:1::/48",
        "2002::/16",
        "2001::/32",
    )
)


class SSRFBlocked(Exception):
    """Kết nối bị chặn vì không phải IP công khai."""


def _is_public_ip(ip_str: str) -> bool:
    """IP literal có phải địa chỉ công khai (is_global) không?"""
    try:
        ip = ipaddress.ip_address(ip_str)
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            return False
        if isinstance(ip, ipaddress.IPv6Address):
            if ip.scope_id or any(ip in net for net in _IPV6_TRANSITION_NETWORKS):
                return False
            if ip.ipv4_mapped:
                return _is_public_ip(str(ip.ipv4_mapped))
        return True
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
        except OSError:
            # gaierror là OSError — fail-closed với mọi lỗi resolve, không chỉ DNS.
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
            if not _is_public_ip(str(ip)):
                raise SSRFBlocked("địa chỉ IP không công khai — chặn kết nối")
            # Dùng dạng chuẩn (bỏ trailing-dot) để backend không hiểu nhầm thành hostname.
            return await self._backend.connect_tcp(
                str(ip),
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )

        ips = await self._resolve_pinned(host_clean)
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
    if not isinstance(url, str) or not url or len(url) > 8192:
        return "URL rỗng hoặc quá dài."
    # Ký tự điều khiển/khoảng trắng/backslash khiến các parser HTTP có thể hiểu
    # URL khác nhau; từ chối thay vì dựa vào chuẩn hoá ngầm của từng thư viện.
    if "\\" in url or any(ord(char) < 33 or char.isspace() for char in url):
        return "URL chứa ký tự không hợp lệ."
    try:
        parts = urlparse(url)
        # .hostname/.port có thể ném ValueError riêng (IPv6 cụt, port ngoài dải, …).
        host = (parts.hostname or "").lower().rstrip(".")
        _ = parts.port  # buộc kiểm tra port ngay tại guard, trước khi gọi Jina/httpx
        userinfo = bool(parts.username or parts.password)
        scheme = parts.scheme
    except ValueError:
        return "URL không hợp lệ."
    if scheme not in ("http", "https"):
        return "Chỉ cho phép URL http/https."
    if userinfo:
        return "URL không được chứa thông tin đăng nhập."
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
    if ip is not None and not _is_public_ip(str(ip)):
        return "Không cho phép tải địa chỉ IP nội bộ."
    return None


class _FetchError(Exception):
    """Lỗi khi tải (kèm lý do tiếng Việt, hiển thị được cho model)."""


@dataclass
class _FetchResult:
    body: bytes
    encoding: str = "utf-8"


async def _fetch_limited(
    client: httpx.AsyncClient, url: str, max_bytes: int = MAX_BYTES
) -> _FetchResult:
    """GET url (kiểm tra an toàn ở MỌI bước chuyển hướng), giới hạn dung lượng."""
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        reason = validate_public_url(current)
        if reason:
            raise _FetchError(reason)
        async with client.stream(
            "GET", current, headers={"User-Agent": _UA, "Accept-Encoding": "identity"}
        ) as resp:
            if resp.status_code in _REDIRECT_STATUSES:
                location = (resp.headers.get("location") or "").strip()
                if not location:
                    raise _FetchError("trang chuyển hướng thiếu địa chỉ đích")
                current = urljoin(current, location)
                continue  # vòng lặp sẽ validate URL đích trước khi tải
            if resp.status_code >= 400:
                raise _FetchError(f"HTTP {resp.status_code}")
            # Never let HTTPX decompress an untrusted chunk before checking its
            # size. Ask for identity and reject servers that ignore the request.
            if resp.headers.get("content-encoding", "identity").strip().lower() != "identity":
                raise _FetchError("trang dùng nội dung nén không được hỗ trợ")
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise _FetchError(f"trang quá lớn (> {max_bytes // (1024 * 1024)}MB)")
                chunks.append(chunk)
            return _FetchResult(body=b"".join(chunks), encoding=resp.encoding or "utf-8")
    raise _FetchError("quá nhiều lần chuyển hướng")


class _PinnedTransport(httpx.AsyncHTTPTransport):
    """Transport gắn sẵn pool chống SSRF — không tạo pool mặc định rồi bỏ."""

    def __init__(self, pool: httpcore.AsyncConnectionPool) -> None:
        # Không gọi super().__init__: AsyncHTTPTransport() sẽ mở pool AnyIO
        # mặc định (không SSRF) rồi bị thay — rò FD nếu không aclose pool cũ.
        self._pool = pool


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
    return httpx.AsyncClient(
        transport=_PinnedTransport(pool),
        timeout=timeout,
        follow_redirects=False,
    )


async def read_page(
    url: str, timeout: float = 30.0, client: httpx.AsyncClient | None = None
) -> str:
    """Bound the whole read, including DNS, redirects and the Jina fallback."""
    try:
        async with asyncio.timeout(timeout):
            return await _read_page(url, timeout, client)
    except TimeoutError:
        return "Không tải được trang: quá thời gian cho phép."


async def _read_page(url: str, timeout: float, client: httpx.AsyncClient | None) -> str:
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
        except Exception as exc:  # noqa: BLE001 — mọi lỗi Jina đều fallback HTML
            logger.debug("Jina Reader lỗi lạ (chuyển fallback): %s", exc)

        # 2) Fallback: tải HTML trực tiếp và parse văn bản
        try:
            result = await _fetch_limited(http, url)
        except (_FetchError, httpx.HTTPError, SSRFBlocked) as exc:
            return f"Không tải được trang: {exc}"
        except Exception as exc:  # noqa: BLE001 — không để lỗi mạng lạ sập tool
            return f"Không tải được trang: {exc}"
        try:
            return await _extract_html(result.body, result.encoding)
        except (_FetchError, ValueError, AssertionError) as exc:
            return f"Không tải được trang: {exc}"
    finally:
        if own_client:
            await http.aclose()


class _HTMLTextExtractor(HTMLParser):
    """Collect a bounded text prefix without building or mutating a DOM tree."""

    _excluded = frozenset({"script", "style", "nav", "footer", "header", "aside", "form"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.length = 0
        self.skip_tag: str | None = None
        self.skip_depth = 0
        self.separator = True

    def handle_starttag(self, tag: str, attrs) -> None:
        self.separator = True
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth += 1
        elif tag in self._excluded:
            self.skip_tag, self.skip_depth = tag, 1

    def handle_endtag(self, tag: str) -> None:
        self.separator = True
        if tag == self.skip_tag:
            self.skip_depth -= 1
            if self.skip_depth == 0:
                self.skip_tag = None

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.separator = True

    def handle_data(self, data: str) -> None:
        if self.skip_tag or self.length >= MAX_CHARS:
            return
        # A data callback may end at a feed boundary in the middle of a word.
        # Insert separators only at HTML node boundaries, not between feeds.
        text = data.lstrip() if self.separator or not self.parts else data
        if not text:
            return
        if self.separator and self.parts and not self.parts[-1][-1].isspace():
            text = " " + text
        text = text[: MAX_CHARS - self.length]
        self.parts.append(text)
        self.length += len(text)
        self.separator = False


async def _extract_html(body: bytes, encoding: str = "utf-8") -> str:
    parser = _HTMLTextExtractor()
    try:
        markup = body.decode(encoding, errors="replace")
    except LookupError:
        markup = body.decode("utf-8", errors="replace")
    for start in range(0, len(markup), 8192):
        parser.feed(markup[start : start + 8192])
        # HTMLParser retains incomplete tags/comments. Bound that buffer too,
        # preventing repeated rescans of an ever-growing malformed token.
        if len(parser.rawdata) > 65536:
            raise _FetchError("HTML chứa thẻ/chú thích quá dài")
        await asyncio.sleep(0)
        if parser.length >= MAX_CHARS:
            break
    else:
        parser.close()
    return _RE_NEWLINES.sub("\n\n", "".join(parser.parts).strip())[:MAX_CHARS]
