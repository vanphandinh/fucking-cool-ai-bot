"""Bộ kiểm thử audit (chạy offline, không cần mạng/khóa).

Chạy:  python tests/run_tests.py
Bao gồm:
1. Config, formatting, context, rate-limit, stats
2. Filters aiogram (allowlist/trigger) — message model thật
3. Reader guard chống SSRF
4. E2E handlers qua Dispatcher.feed_update với fake Telegram session
5. AI router (fallback + tool-calling) với mock OpenAI server
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from aiogram.types import Message  # noqa: F401 — dùng trong type-hint

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "123:test")
os.environ.setdefault("ALLOWED_GROUP_IDS", "-100200")
os.environ.setdefault("ADMIN_IDS", "42")
os.environ.setdefault("GEMINI_API_KEY", "gk")
os.environ.setdefault("GROQ_API_KEY", "gk")
os.environ.setdefault("SEARCH_BACKEND", "ddgs")

PASSED = 0
FAILED = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✔ {name}")
    else:
        FAILED += 1
        print(f"  ✘ {name} {extra}")


# ----------------------------------------------------------------------
def t_config_formatting():
    print("== Config & formatting ==")
    from app.config import Settings

    s = Settings()
    check("allowlist parse", s.allowed_group_ids_list == [-100200])
    check("admin parse", s.admin_ids_list == [42])
    s.bot_username = "FuckingCoolAIbot"
    check("username clean", s.bot_username_clean == "fuckingcoolaibot")

    # SEARCH_BACKEND: chấp nhận viết hoa, từ chối giá trị lạ
    old_backend = os.environ.get("SEARCH_BACKEND")
    try:
        os.environ["SEARCH_BACKEND"] = "TAVILY"
        check("search_backend chuẩn hoá", Settings().search_backend == "tavily")
        os.environ["SEARCH_BACKEND"] = "ddg"
        try:
            Settings()
            check("search_backend lạ bị từ chối", False)
        except ValueError:
            check("search_backend lạ bị từ chối", True)
        os.environ["SEARCH_BACKEND"] = "ddgs"
        os.environ["LOG_LEVEL"] = "WARN"
        check("log_level WARN -> WARNING", Settings().log_level == "WARNING")
        os.environ["LOG_LEVEL"] = "NOPE"
        try:
            Settings()
            check("log_level lạ bị từ chối", False)
        except ValueError:
            check("log_level lạ bị từ chối", True)
        os.environ.pop("LOG_LEVEL", None)
        try:
            Settings(request_timeout_sec=-1)
            check("timeout âm bị từ chối", False)
        except ValueError:
            check("timeout âm bị từ chối", True)
    finally:
        if old_backend is None:
            os.environ.pop("SEARCH_BACKEND", None)
        else:
            os.environ["SEARCH_BACKEND"] = old_backend
        os.environ.pop("LOG_LEVEL", None)

    from app.core.formatting import clean_question, format_sources, split_plain

    q = clean_question("@FuckingCoolAIbot  giá vàng hôm nay ?", "FuckingCoolAIbot")
    check("clean_question bỏ mention", q == "giá vàng hôm nay", repr(q))
    q_neg = clean_question("@bot -1 + 2 bằng bao nhiêu", "bot")
    check("clean_question giữ số âm", q_neg == "-1 + 2 bằng bao nhiêu", repr(q_neg))
    check("clean_question mention-only thành rỗng", clean_question("@bot...", "bot") == "")
    check("clean_question bỏ chấm cuối", clean_question("@bot giá vàng.", "bot") == "giá vàng")
    parts = split_plain("y" * 10000, 3900)
    check("split_plain giữ nguyên nội dung", "".join(parts) == "y" * 10000)
    check("split_plain không quá 3900", all(len(p) <= 3900 for p in parts))
    from app.core.formatting import _utf16_len

    emoji_parts = split_plain("😀" * 2500, 3900)
    check(
        "split_plain emoji theo UTF-16 Telegram",
        len(emoji_parts) >= 2 and all(_utf16_len(p) <= 3900 for p in emoji_parts),
        str([_utf16_len(p) for p in emoji_parts]),
    )
    footer = format_sources(
        [{"title": "A", "url": "https://a.com"}, {"title": "B", "url": "https://a.com"}]
    )
    check("format_sources dedupe", footer.count("https://") == 1 and "📚" in footer)


def t_core():
    print("== Context / Limiter / Stats ==")
    from app.core.context import ChatMemory
    from app.core.rate_limiter import RateLimiter
    from app.core.stats import Stats

    m = ChatMemory(max_turns_per_chat=2)
    for r, c in [("user", "h1"), ("assistant", "a1"), ("user", "h2")]:
        m.push(1, r, c)
    h = m.history_for(1)
    check("context giới hạn cặp", len(h) <= 4 and h[-1]["content"] == "h2")
    check("context limit 0 rỗng", m.history_for(1, 0) == [])

    rl = RateLimiter(max_requests_per_min=2)
    check("rate limit allow 2", rl.allow(7)[0] and rl.allow(7)[0] and not rl.allow(7)[0])
    rl0 = RateLimiter(max_requests_per_min=0)
    check("rate limit 0 không crash, từ chối", rl0.allow(1) == (False, 60.0))

    st = Stats()
    st.record_question()
    st.record_answer("gemini")
    check("stats ghi nhận", st.questions_total == 1 and st.last_provider == "gemini")
    from datetime import timedelta

    st._day = st._day - timedelta(days=1)
    check(
        "stats hôm nay roll qua nửa đêm",
        st.live_questions_today() == 0 and st.questions_total == 1,
    )

    from app.core.orchestrator import _format_search_results

    empty = _format_search_results("xyz", [])
    check(
        "search rỗng không bảo dựa vào kết quả",
        "Không có kết quả" in empty and "dựa vào" not in empty,
        empty,
    )


def t_reader_guard():
    print("== SSRF guard ==")
    from app.search.reader import validate_public_url

    good = validate_public_url("https://example.com/a?b=1")
    check("cho phép https public", good is None, str(good))
    check("chặn localhost", validate_public_url("http://localhost:8000/x") is not None)
    check("chặn ip private", validate_public_url("http://10.0.0.5/x") is not None)
    check("chặn 169.254", validate_public_url("http://169.254.169.254/latest") is not None)
    check("chặn loopback", validate_public_url("http://127.0.0.1/x") is not None)
    check("chặn userinfo", validate_public_url("http://user:pass@example.com/") is not None)
    check("chặn scheme lạ", validate_public_url("file:///etc/passwd") is not None)
    check("chặn rỗng", validate_public_url("") is not None)

    # --- Dạng IP viết tắt mà ipaddress không parse nhưng glibc resolve về nội bộ
    for bad in [
        "http://127.1/x",
        "http://127.1.2/x",  # -> 127.1.0.2
        "http://2130706433/",  # -> 127.0.0.1
        "http://2130706434/",  # -> 127.0.0.2
        "http://0x7f000001/",  # -> 127.0.0.1
        "http://0x7f.1/",  # -> 127.0.0.1
        "http://0177.0.0.1/",  # octal -> 127.0.0.1
        "http://0/",  # -> 0.0.0.0
        "http://999.999.999.999/",  # số không hợp lệ
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped loopback
        "http://[::1]/",
        "http://[fe80::1%25lo0]/",  # IPv6 zone index
        "http://[2001:db8::1]/",  # dải tài liệu
    ]:
        check(
            f"chặn dạng IP lạ {bad.split('/')[2][:28]}",
            validate_public_url(bad) is not None,
            str(validate_public_url(bad)),
        )

    # IP public dạng literal vẫn được phép (1.2.3 nở thành 1.2.0.3 — public)
    check("cho phép ip public 8.8.8.8", validate_public_url("http://8.8.8.8/") is None)
    check("cho phép dạng số public 1.2.3", validate_public_url("http://1.2.3/") is None)
    check("cho phép ipv6 public", validate_public_url("http://[2606:4700:4700::1111]/") is None)


def t_reader_dns_rebinding():
    print("== Chống DNS-rebinding / IP pinning ==")
    import socket

    import app.search.reader as mod

    real_getaddrinfo = socket.getaddrinfo

    async def run():
        def fake_resolver(*ips, error=None):
            def _fake(host, *args, **kwargs):
                if error is not None:
                    raise error
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]

            return _fake

        try:
            # _is_public_ip
            check("is_public: 8.8.8.8", mod._is_public_ip("8.8.8.8"))
            check("is_public: 127.0.0.1", not mod._is_public_ip("127.0.0.1"))
            check("is_public: ::ffff:127.0.0.1", not mod._is_public_ip("::ffff:127.0.0.1"))
            check("is_public: ::1", not mod._is_public_ip("::1"))
            check("is_public: 2606:4700::1111", mod._is_public_ip("2606:4700:4700::1111"))

            # _resolve_all THẬT + getaddrinfo giả: chỉ giữ IP public
            socket.getaddrinfo = fake_resolver("127.0.0.1")
            check("resolve toàn bộ private -> rỗng", await mod._resolve_all("evil.nip.io") == [])
            socket.getaddrinfo = fake_resolver("10.0.0.1", "127.0.0.1")
            check("chỉ private -> rỗng", await mod._resolve_all("evil.sslip.io") == [])
            socket.getaddrinfo = fake_resolver("8.8.8.8", "127.0.0.1")
            got = await mod._resolve_all("mixed.example")
            check("có IP public -> giữ IP public", got == ["8.8.8.8"], str(got))
            socket.getaddrinfo = fake_resolver(error=socket.gaierror(-2, "DNS chết"))
            check(
                "DNS chết -> rỗng (fail-closed, không resolve lần 2)",
                await mod._resolve_all("offline.example") == [],
            )
            socket.getaddrinfo = fake_resolver(error=OSError("resolver exploded"))
            check(
                "OSError resolve -> rỗng (fail-closed)",
                await mod._resolve_all("boom.example") == [],
            )
        finally:
            socket.getaddrinfo = real_getaddrinfo

        # Backend thật phải pin IP public vào inner AnyIOBackend, không dùng lớp abstract.
        class FakeInner:
            def __init__(self) -> None:
                self.hosts: list[str] = []

            async def connect_tcp(
                self, host, port, timeout=None, local_address=None, socket_options=None
            ):
                self.hosts.append(host)
                return f"stream:{host}:{port}"

            async def sleep(self, seconds: float) -> None:
                return None

        inner = FakeInner()
        backend = mod.SSRFCheckBackend(inner)  # type: ignore[arg-type]
        try:
            await backend.connect_tcp("127.0.0.1", 80)
            check("backend chặn IP private", False)
        except mod.SSRFBlocked:
            check("backend chặn IP private", True)
        check("backend private không gọi inner", inner.hosts == [])

        inner.hosts.clear()
        got = await backend.connect_tcp("8.8.8.8", 443)
        check(
            "backend IP public ủy quyền inner",
            inner.hosts == ["8.8.8.8"] and got == "stream:8.8.8.8:443",
            str(inner.hosts),
        )

        def fake_pub(host, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 0))]

        socket.getaddrinfo = fake_pub
        try:
            inner.hosts.clear()
            b2 = mod.SSRFCheckBackend(inner)  # type: ignore[arg-type]
            await b2.connect_tcp("example.com", 443)
            check("backend hostname pin IP public", inner.hosts == ["1.2.3.4"], str(inner.hosts))
        finally:
            socket.getaddrinfo = real_getaddrinfo

        def fake_priv(host, *args, **kwargs):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]

        socket.getaddrinfo = fake_priv
        try:
            inner.hosts.clear()
            b3 = mod.SSRFCheckBackend(inner)  # type: ignore[arg-type]
            try:
                await b3.connect_tcp("evil.nip.io", 80)
                check("backend hostname private bị chặn", False)
            except mod.SSRFBlocked:
                check("backend hostname private bị chặn", True)
            check("backend hostname private không kết nối", inner.hosts == [])
        finally:
            socket.getaddrinfo = real_getaddrinfo

        import httpcore

        check(
            "inner mặc định là AnyIOBackend (không phải lớp abstract)",
            isinstance(mod.SSRFCheckBackend()._backend, httpcore.AnyIOBackend),
        )

        client = mod._build_client(5.0)
        try:
            check(
                "_build_client không tạo pool mặc định rồi thay",
                isinstance(client._transport, mod._PinnedTransport),
            )
            pool = client._transport._pool
            check(
                "_build_client pool dùng SSRFCheckBackend",
                isinstance(pool._network_backend, mod.SSRFCheckBackend),
            )
        finally:
            await client.aclose()

    asyncio.run(run())


def t_reader_fetch():
    print("== Reader fetch (MockTransport, offline) ==")
    import httpx

    import app.search.reader as mod
    from app.search.reader import read_page

    requested: list[str] = []

    def _html(body: str) -> bytes:
        return f"<html><head><style>a{{}}</style></head><body>{body}</body></html>".encode()

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        host = request.url.host
        path = request.url.path
        if host == "r.jina.ai":
            return httpx.Response(429, json={"error": "rate"}, request=request)
        if host == "ok.test":
            if path == "/redir-public":
                return httpx.Response(302, headers={"Location": "/final"}, request=request)
            if path == "/redir-other":
                return httpx.Response(
                    302, headers={"Location": "https://other.test/landing"}, request=request
                )
            if path == "/redir-ssrf":
                return httpx.Response(
                    302, headers={"Location": "http://127.0.0.1:9999/x"}, request=request
                )
            if path == "/redir-shorthand":
                return httpx.Response(
                    302, headers={"Location": "http://2130706433/x"}, request=request
                )
            if path == "/big":
                return httpx.Response(200, content=b"x" * (mod.MAX_BYTES + 100), request=request)
            if path == "/error":
                return httpx.Response(500, text="boom", request=request)
            return httpx.Response(
                200,
                content=_html("<h1>Chào bạn</h1><p>Nội dung <b>quan trọng</b> ở đây.</p>"),
                request=request,
            )
        if host in ("127.0.0.1", "2130706433"):
            # Nếu guard lọt, transport sẽ "chạm" vào host nội bộ — test phải fail
            return httpx.Response(200, content=b"INTERNAL-LEAK", request=request)
        if host == "other.test":
            return httpx.Response(200, content=_html("<p>Trang khác OK</p>"), request=request)
        return httpx.Response(404, request=request)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=5, follow_redirects=False
        ) as client:
            try:
                text = await read_page("https://ok.test/", timeout=5, client=client)
                check(
                    "fallback parse html (jina 429)",
                    "Chào bạn" in text and "quan trọng" in text and "style" not in text,
                    text[:80],
                )

                requested.clear()
                text = await read_page("https://ok.test/redir-public", timeout=5, client=client)
                check("redirect public cùng host", "Chào bạn" in text, text[:80])

                requested.clear()
                text = await read_page("https://ok.test/redir-other", timeout=5, client=client)
                check("redirect sang host public khác", "Trang khác OK" in text, text[:80])

                requested.clear()
                text = await read_page("https://ok.test/redir-ssrf", timeout=5, client=client)
                check(
                    "redirect vào 127.0.0.1 bị chặn",
                    "Không tải được" in text and "INTERNAL-LEAK" not in text,
                    text[:120],
                )

                requested.clear()
                text = await read_page("https://ok.test/redir-shorthand", timeout=5, client=client)
                check(
                    "redirect dạng số lạ bị chặn",
                    "Không tải được" in text and "INTERNAL-LEAK" not in text,
                    text[:120],
                )
                check(
                    "không request nào tới host nội bộ",
                    not any(
                        "127.0.0.1" in u or u.startswith("http://2130706433") for u in requested
                    ),
                    str(requested),
                )

                text = await read_page("https://ok.test/big", timeout=5, client=client)
                check("trang quá lớn bị chặn", "quá lớn" in text, text[:80])

                text = await read_page("https://ok.test/error", timeout=5, client=client)
                check("HTTP 500 báo lỗi rõ", "HTTP 500" in text, text[:80])

                text = await read_page("http://127.0.0.1:9999/x", timeout=5, client=client)
                check("read_page tự chặn URL nội bộ", "Không tải được" in text, text[:80])
            finally:
                pass

    asyncio.run(run())


# ----------------------------------------------------------------------
def _msg(
    chat_id: int,
    chat_type: str,
    text: str,
    reply_to: "Message | None" = None,
    caption: str | None = None,
) -> "Message":
    from aiogram.types import Chat, Message, User

    chat = Chat(id=chat_id, type=chat_type, title="G" if chat_type != "private" else None)
    sender = User(id=1, is_bot=False, first_name="A")
    return Message(
        message_id=10,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=sender,
        text=text,
        caption=caption,
        reply_to_message=reply_to,
    )


def _mk_reply(chat, text: str, username: str) -> "Message":
    from aiogram.types import Message, User

    bot_user = User(id=99, is_bot=True, first_name="Bot", username=username)
    return Message(
        message_id=5,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=bot_user,
        text=text,
    )


def t_filters():
    print("== Filters ==")
    from app.config import Settings
    from app.bot.filters import AllowedChat, TriggeredMessage

    s = Settings()
    s.bot_username = "FuckingCoolAIbot"
    allowed = AllowedChat(s)
    trig = TriggeredMessage(s)

    async def run():
        # AllowedChat
        check("cho phép group đúng", await allowed(_msg(-100200, "supergroup", "x")))
        check("chặn group lạ", not await allowed(_msg(-999, "supergroup", "x @FuckingCoolAIbot")))
        check("chặn private", not await allowed(_msg(1234, "private", "hi")))
        # TriggeredMessage
        check(
            "mention kích hoạt", await trig(_msg(-100200, "supergroup", "giá @FuckingCoolAIbot?"))
        )
        check(
            "mention viết thường kích hoạt",
            await trig(_msg(-100200, "supergroup", "xin chào @fuckingcoolaibot nha")),
        )
        check(
            "mention cuối câu (dấu chấm) kích hoạt",
            await trig(_msg(-100200, "supergroup", "thế @FuckingCoolAIbot.")),
        )
        check(
            "mention trong caption kích hoạt",
            await trig(
                _msg(-100200, "supergroup", None, caption="mô tả ảnh @FuckingCoolAIbot xem giúp")
            ),
        )
        check("không trigger -> false", not await trig(_msg(-100200, "supergroup", "chào")))
        check(
            "username dài hơn (tiền tố) -> false",
            not await trig(_msg(-100200, "supergroup", "nói gì @FuckingCoolAIbotXYZ")),
        )
        chat = _msg(-100200, "supergroup", "x").chat
        reply_bot = _mk_reply(chat, "câu trả lời", "FuckingCoolAIbot")
        check(
            "reply tin bot kích hoạt",
            await trig(_msg(-100200, "supergroup", "tiếp theo?", reply_to=reply_bot)),
        )
        chat2 = _msg(-100200, "supergroup", "x").chat
        # reply tin của người khác, không mention -> không trigger
        m_other = _mk_reply(chat2, "tin thành viên", "some_user")
        m_other = _msg(-100200, "supergroup", "thắc mắc", reply_to=m_other)
        check("reply tin người khác (không mention) -> false", not await trig(m_other))
        check("slash -> false", not await trig(_msg(-100200, "supergroup", "/ask x")))

    asyncio.run(run())


# ----------------------------------------------------------------------
class FakeTelegramSession:
    """Session giả — ghi lại mọi TelegramMethod được gọi, không gửi mạng."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, bot, method, timeout=None):
        name = type(method).__name__
        data = method.model_dump(exclude_none=True)
        self.calls.append((name, data))
        # Trả về kết quả tối thiểu (handler không dùng tới giá trị)
        if name == "GetMe":
            from aiogram.types import User

            return User(id=42, is_bot=True, first_name="Bot", username="FuckingCoolAIbot")
        return True

    async def close(self) -> None:  # noqa: BLE001
        return None


def t_e2e_handlers():
    print("== E2E handlers (feed_update + fake session) ==")
    from aiogram import Bot, Dispatcher
    from aiogram.types import (
        Chat,
        ChatMemberLeft,
        ChatMemberMember,
        ChatMemberUpdated,
        Message,
        Update,
        User,
    )

    from app.config import Settings
    from app.core.context import ChatMemory
    from app.core.orchestrator import Answer
    from app.core.rate_limiter import RateLimiter
    from app.core.stats import Stats
    from app.bot.handlers import build_lifecycle_router, build_message_router

    class FakeOrch:
        def __init__(self) -> None:
            self.received: list[dict] = []

        async def ask(self, question, history=None, quoted=None):
            self.received.append({"q": question, "quoted": quoted})
            if question == "LONGTEXT":
                await asyncio.sleep(0.05)  # cho typing loop kịp gửi chat action
                return Answer(text="y" * 8000, provider="gemini", searched=False, sources=[])
            txt = f"TRẢ LỜI: {question[:30]}"
            if quoted:
                txt += f" [đã đọc tin reply: {quoted[:30]}]"
            return Answer(text=txt, provider="gemini", searched=False, sources=[])

    async def run():
        s = Settings()
        s.bot_username = "FuckingCoolAIbot"
        orch = FakeOrch()
        memory = ChatMemory(max_turns_per_chat=3)
        limiter = RateLimiter(max_requests_per_min=50)
        stats = Stats()

        session = FakeTelegramSession()
        bot = Bot(token="123:test", session=session)

        dp = Dispatcher()
        dp.include_router(
            build_message_router(s, orch, memory, limiter, stats)  # type: ignore[arg-type]
        )
        dp.include_router(build_lifecycle_router(s))

        def mk_update(chat_id: int, chat_type: str, text: str, reply=None, update_id=1):
            return Update(
                update_id=update_id,
                message=_msg(chat_id, chat_type, text, reply_to=reply),
            )

        # 1) /help trong group được phép
        up = mk_update(-100200, "supergroup", "/help", update_id=1)
        await dp.feed_update(bot, up)
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check("E2E: /help trả lời", any("trợ lý AI" in c[1].get("text", "") for c in sent))

        session.calls.clear()
        up = mk_update(-100200, "supergroup", "/HELP", update_id=101)
        await dp.feed_update(bot, up)
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check("E2E: /HELP ignore_case", any("trợ lý AI" in c[1].get("text", "") for c in sent))

        # 2) mention câu hỏi -> trả lời, ghi memory
        session.calls.clear()
        up = mk_update(-100200, "supergroup", "giá vàng @FuckingCoolAIbot?", update_id=2)
        await dp.feed_update(bot, up)
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check(
            "E2E: mention trả lời", any("TRẢ LỜI: giá vàng" in c[1].get("text", "") for c in sent)
        )
        check(
            "E2E: memory đã lưu câu hỏi",
            memory.history_for(-100200) and memory.history_for(-100200)[-1]["role"] == "assistant",
        )

        # 3) group KHÔNG được phép -> im lặng hoàn toàn
        session.calls.clear()
        up = mk_update(-999, "supergroup", "giá vàng @FuckingCoolAIbot?", update_id=3)
        await dp.feed_update(bot, up)
        check("E2E: group lạ im lặng (0 gọi API)", len(session.calls) == 0, str(session.calls))

        # 4) private chat mention -> im lặng
        session.calls.clear()
        up = mk_update(5555, "private", "xin chào @FuckingCoolAIbot", update_id=4)
        await dp.feed_update(bot, up)
        check("E2E: private chat im lặng", len(session.calls) == 0, str(session.calls))

        # 5) reply tin bot (không mention) -> đọc quoted
        session.calls.clear()
        chat = Chat(id=-100200, type="supergroup", title="G")
        reply_bot = _mk_reply(chat, "nội dung cũ của bot", "FuckingCoolAIbot")
        up = mk_update(-100200, "supergroup", "thế còn VN?", reply=reply_bot, update_id=5)
        await dp.feed_update(bot, up)
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        reply_texts = [c[1].get("text", "") for c in sent]
        check(
            "E2E: reply tin bot đọc ngữ cảnh",
            any("đã đọc tin reply: nội dung cũ của bot" in t for t in reply_texts),
            str(reply_texts[:1]),
        )

        # 6) /status của admin
        session.calls.clear()
        chat = Chat(id=-100200, type="supergroup", title="G")
        admin_msg = Message(
            message_id=20,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=User(id=42, is_bot=False, first_name="Admin"),
            text="/status",
        )
        await dp.feed_update(bot, Update(update_id=6, message=admin_msg))
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check("E2E: /status admin", any("Trạng thái bot" in c[1].get("text", "") for c in sent))

        # 7) lifecycle: bot bị thêm vào group LẠ -> tự leave
        session.calls.clear()
        upd = ChatMemberUpdated(
            chat=Chat(id=-777, type="supergroup", title="Group Lạ"),
            from_user=User(id=9, is_bot=False, first_name="X"),
            date=datetime.now(timezone.utc),
            old_chat_member=ChatMemberLeft(
                user=User(id=99, is_bot=True, first_name="Bot"),
            ),
            new_chat_member=ChatMemberMember(
                user=User(id=99, is_bot=True, first_name="Bot"),
            ),
        )
        await dp.feed_update(bot, Update(update_id=7, my_chat_member=upd))
        leaves = [c for c in session.calls if c[0] == "LeaveChat"]
        check(
            "E2E: tự rời group lạ",
            len(leaves) == 1 and leaves[0][1].get("chat_id") == -777,
            str(leaves),
        )

        # 8) lifecycle: bot thêm vào group ĐÚNG allowlist -> không rời
        session.calls.clear()
        upd2 = ChatMemberUpdated(
            chat=Chat(id=-100200, type="supergroup", title="Group Đúng"),
            from_user=User(id=9, is_bot=False, first_name="X"),
            date=datetime.now(timezone.utc),
            old_chat_member=ChatMemberLeft(user=User(id=99, is_bot=True, first_name="Bot")),
            new_chat_member=ChatMemberMember(user=User(id=99, is_bot=True, first_name="Bot")),
        )
        await dp.feed_update(bot, Update(update_id=8, my_chat_member=upd2))
        check(
            "E2E: group đúng không bị rời",
            not any(c[0] == "LeaveChat" for c in session.calls),
            str(session.calls),
        )

        # 9) /ask kèm câu hỏi -> orchestrator nhận đúng câu hỏi
        session.calls.clear()
        orch.received.clear()
        up = mk_update(-100200, "supergroup", "/ask thời tiết hôm nay?", update_id=9)
        await dp.feed_update(bot, up)
        check(
            "E2E: /ask có args",
            bool(orch.received) and orch.received[-1]["q"] == "thời tiết hôm nay?",
            str(orch.received),
        )
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check("E2E: /ask trả lời", any("TRẢ LỜI: thời tiết" in c[1].get("text", "") for c in sent))

        session.calls.clear()
        orch.received.clear()
        chat_ask = Chat(id=-100200, type="supergroup", title="G")
        reply_for_ask = _mk_reply(chat_ask, "nội dung cũ của bot", "FuckingCoolAIbot")
        up = mk_update(
            -100200, "supergroup", "/ask thế còn VN?", reply=reply_for_ask, update_id=102
        )
        await dp.feed_update(bot, up)
        check(
            "E2E: /ask khi reply đọc quoted",
            bool(orch.received)
            and orch.received[-1]["q"] == "thế còn VN?"
            and orch.received[-1]["quoted"] == "nội dung cũ của bot",
            str(orch.received),
        )

        # 10) /ask không có câu hỏi -> nhắc cách dùng
        session.calls.clear()
        orch.received.clear()
        up = mk_update(-100200, "supergroup", "/ask", update_id=10)
        await dp.feed_update(bot, up)
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check("E2E: /ask trống -> hướng dẫn", any("hỏi gì" in c[1].get("text", "") for c in sent))
        check("E2E: /ask trống không gọi orchestrator", not orch.received)

        # 11) /ask@TenBot (mention đúng bot) vẫn chạy
        session.calls.clear()
        orch.received.clear()
        up = mk_update(-100200, "supergroup", "/ask@FuckingCoolAIbot giá vàng?", update_id=11)
        await dp.feed_update(bot, up)
        check(
            "E2E: /ask@Bot đúng mention",
            bool(orch.received) and orch.received[-1]["q"] == "giá vàng?",
            str(orch.received),
        )

        # 12) /ask@BotKhác -> phải IM LẶNG (không trả lời lệnh của bot khác)
        session.calls.clear()
        orch.received.clear()
        up = mk_update(-100200, "supergroup", "/ask@SomeOtherBot xin chào", update_id=12)
        await dp.feed_update(bot, up)
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check(
            "E2E: /ask@bot khác im lặng",
            not sent and not orch.received,
            str([c[0] for c in session.calls]),
        )

        # 13) /status của người KHÔNG phải admin -> im lặng
        session.calls.clear()
        non_admin = Message(
            message_id=21,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-100200, type="supergroup", title="G"),
            from_user=User(id=1, is_bot=False, first_name="Member"),
            text="/status",
        )
        await dp.feed_update(bot, Update(update_id=13, message=non_admin))
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check("E2E: /status non-admin im lặng", not sent, str(session.calls))

        # 14) handle dài hơn username bot (@FuckingCoolAIbotXYZ) -> KHÔNG trigger
        session.calls.clear()
        orch.received.clear()
        up = mk_update(-100200, "supergroup", "này @FuckingCoolAIbotXYZ là ai?", update_id=14)
        await dp.feed_update(bot, up)
        check(
            "E2E: handle dài hơn không trigger",
            not orch.received and not any(c[0] == "SendMessage" for c in session.calls),
            str([c[0] for c in session.calls]),
        )

        # 15) lifecycle + LEARN_GROUP_ID_MODE=1: group lạ -> log, KHÔNG tự rời
        s_learn = Settings(learn_group_id_mode=True)
        session3 = FakeTelegramSession()
        bot3 = Bot(token="123:test", session=session3)
        dp3 = Dispatcher()
        dp3.include_router(build_lifecycle_router(s_learn))
        upd3 = ChatMemberUpdated(
            chat=Chat(id=-666, type="supergroup", title="Group Học ID"),
            from_user=User(id=9, is_bot=False, first_name="X"),
            date=datetime.now(timezone.utc),
            old_chat_member=ChatMemberLeft(user=User(id=99, is_bot=True, first_name="Bot")),
            new_chat_member=ChatMemberMember(user=User(id=99, is_bot=True, first_name="Bot")),
        )
        await dp3.feed_update(bot3, Update(update_id=15, my_chat_member=upd3))
        check(
            "E2E: learn-mode không rời group lạ",
            not any(c[0] == "LeaveChat" for c in session3.calls),
            str(session3.calls),
        )
        await session3.close()

        # 16) lifecycle: bot bị thêm vào CHANNEL lạ -> tự rời luôn
        session.calls.clear()
        upd4 = ChatMemberUpdated(
            chat=Chat(id=-888, type="channel", title="Kênh Lạ"),
            from_user=User(id=9, is_bot=False, first_name="X"),
            date=datetime.now(timezone.utc),
            old_chat_member=ChatMemberLeft(user=User(id=99, is_bot=True, first_name="Bot")),
            new_chat_member=ChatMemberMember(user=User(id=99, is_bot=True, first_name="Bot")),
        )
        await dp.feed_update(bot, Update(update_id=16, my_chat_member=upd4))
        leaves = [c for c in session.calls if c[0] == "LeaveChat"]
        check(
            "E2E: tự rời channel lạ",
            len(leaves) == 1 and leaves[0][1].get("chat_id") == -888,
            str(leaves),
        )

        # 17) mention trong CAPTION (tin kèm ảnh) -> vẫn hỏi được, mention bị strip
        session.calls.clear()
        orch.received.clear()
        cap_msg = Message(
            message_id=30,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-100200, type="supergroup", title="G"),
            from_user=User(id=1, is_bot=False, first_name="A"),
            text=None,
            caption="bức ảnh này chụp ở đâu @FuckingCoolAIbot?",
        )
        await dp.feed_update(bot, Update(update_id=17, message=cap_msg))
        check(
            "E2E: mention trong caption trả lời",
            bool(orch.received) and orch.received[-1]["q"] == "bức ảnh này chụp ở đâu",
            str(orch.received),
        )
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check("E2E: caption có tin trả lời", any("TRẢ LỜI:" in c[1].get("text", "") for c in sent))

        # 18) group bật Topics + câu trả lời dài -> các phần gửi tiếp phải kèm
        # message_thread_id của topic gốc (không rơi vào General)
        session.calls.clear()
        orch.received.clear()
        topic_msg = Message(
            message_id=31,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-100200, type="supergroup", title="G"),
            from_user=User(id=1, is_bot=False, first_name="A"),
            text="LONGTEXT @FuckingCoolAIbot",
            message_thread_id=555,
        )
        await dp.feed_update(bot, Update(update_id=18, message=topic_msg))
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        extra_parts = [c for c in sent if c[1].get("message_thread_id") == 555]
        check(
            "E2E: topic — trả lời dài cắt nhiều phần",
            len(extra_parts) >= 1 and len(sent) >= 2,
            str(len(sent)),
        )
        # phần đầu là reply (aiogram tự giữ topic), phần sau phải có thread id
        check(
            "E2E: topic — mọi phần sau đều có message_thread_id",
            all(c[1].get("message_thread_id") == 555 for c in extra_parts),
        )
        actions = [c for c in session.calls if c[0] == "SendChatAction"]
        check(
            "E2E: topic — typing có message_thread_id",
            any(c[1].get("message_thread_id") == 555 for c in actions),
            str(actions[:2]),
        )

        await session.close()

    asyncio.run(run())


# ----------------------------------------------------------------------
def t_ai_router_mock():
    print("== AI router (mock OpenAI server) ==")
    STATE = {"gemini": 0, "groq": 0, "plain": 0, "loop_n": 0, "loop_with_tools": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: D401
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length))
            model = req["model"]
            tools = bool(req.get("tools"))
            if model == "gemini-429":
                STATE["gemini"] += 1
                return self._send({"error": {"message": "rate limited"}}, 429)
            if model == "no-tools":
                if tools:
                    return self._send(
                        {"error": {"message": "does not support function calling"}}, 400
                    )
                STATE["plain"] += 1
                return self._send({"choices": [{"message": {"content": "plain answer"}}]})
            if model == "loop-tools":
                # model "kẹt vòng lặp": có tools thì gọi mãi, không tools mới trả lời
                STATE["loop_n"] += 1
                STATE["loop_with_tools"].append(tools)
                if tools:
                    return self._send(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": None,
                                        "tool_calls": [
                                            {
                                                "id": "c1",
                                                "type": "function",
                                                "function": {
                                                    "name": "web_search",
                                                    "arguments": '{"query":"x"}',
                                                },
                                            }
                                        ],
                                    }
                                }
                            ]
                        }
                    )
                return self._send({"choices": [{"message": {"content": "loop-plain-answer"}}]})
            # model tool-user
            STATE["groq"] += 1
            last = req["messages"][-1]
            if last["role"] == "user":
                return self._send(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "type": "function",
                                            "function": {
                                                "name": "web_search",
                                                "arguments": '{"query":"x"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                )
            if last["role"] == "tool":
                check(
                    "mock: tool result truyền lại model",
                    "Kết quả" in last.get("content", ""),
                    last.get("content", "")[:60],
                )
                return self._send({"choices": [{"message": {"content": "FINAL"}}]})
            return self._send({"choices": [{"message": {"content": "other"}}]})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from app.ai.base import AllProvidersFailed, OpenAICompatProvider
        from app.ai.router import AIProviderRouter

        base = f"http://127.0.0.1:{port}"
        tools = [
            {
                "type": "function",
                "function": {"name": "web_search", "parameters": {"type": "object"}},
            }
        ]

        async def ex(name, args):
            return "Kết quả tìm kiếm: 1. A"

        async def run():
            # fallback 429 -> tool-loop -> FINAL
            r1 = AIProviderRouter(
                [
                    OpenAICompatProvider("g", base, "k", "gemini-429", timeout=5),
                    OpenAICompatProvider("groq", base, "k", "tool-user", timeout=5),
                ]
            )
            text, prov = await r1.complete([{"role": "user", "content": "hi"}], tools, ex)
            check(
                "fallback 429->groq + tool loop",
                text == "FINAL" and prov == "groq",
                f"{text}/{prov}",
            )

            # provider không hỗ trợ tools -> retry plain, và bị tắt tools VĨNH VIỄN
            p_nt = OpenAICompatProvider("nt", base, "k", "no-tools", timeout=5)
            r2 = AIProviderRouter([p_nt])
            text2, prov2 = await r2.complete([{"role": "user", "content": "hi"}], tools, ex)
            check("retry không tools", text2 == "plain answer")
            check("provider không tools bị tắt vĩnh viễn", p_nt.supports_tools is False)

            # tất cả 429 -> AllProvidersFailed
            r3 = AIProviderRouter([OpenAICompatProvider("g", base, "k", "gemini-429", timeout=5)])
            try:
                await r3.complete([{"role": "user", "content": "hi"}], tools, ex)
                check("AllProvidersFailed ném", False)
            except AllProvidersFailed:
                check("AllProvidersFailed ném", True)

            # model kẹt vòng lặp gọi tool: đúng max_tool_rounds lượt thực thi,
            # sau đó retry KHÔNG tools (1 lần) và KHÔNG tắt tools vĩnh viễn
            STATE["loop_n"] = 0
            STATE["loop_with_tools"] = []
            p_loop = OpenAICompatProvider("loop", base, "k", "loop-tools", timeout=5)
            r4 = AIProviderRouter([p_loop], max_tool_rounds=2)
            text4, prov4 = await r4.complete([{"role": "user", "content": "hi"}], tools, ex)
            # 3 lượt gọi có tools (vòng 0,1,2 — vòng 2 dừng trước khi thực thi lượt 3)
            # + 1 lượt retry không tools = 4 request
            check(
                "tool-loop: retry không tools sau khi đủ vòng",
                text4 == "loop-plain-answer" and prov4 == "loop" and STATE["loop_n"] == 4,
                f"{text4} n={STATE['loop_n']}",
            )
            check(
                "tool-loop: supports_tools vẫn True (chỉ retry 1 lần)",
                p_loop.supports_tools is True,
            )
            # request cuối cùng (retry) không kèm tools
            check(
                "tool-loop: request retry không có tools",
                STATE["loop_with_tools"]
                and STATE["loop_with_tools"][-1] is False
                and STATE["loop_with_tools"][0] is not False,
                str(STATE["loop_with_tools"]),
            )

        asyncio.run(run())
    finally:
        server.shutdown()


def t_normalize_content():
    print("== AI content normalize ==")
    from app.ai.base import _normalize_content

    check("_normalize_content str", _normalize_content("hi") == "hi")
    check(
        "_normalize_content list parts",
        _normalize_content([{"type": "text", "text": "A"}, {"text": "B"}]) == "AB",
    )
    check("_normalize_content None", _normalize_content(None) is None)


if __name__ == "__main__":
    t_config_formatting()
    t_core()
    t_reader_guard()
    t_reader_dns_rebinding()
    t_reader_fetch()
    t_filters()
    t_e2e_handlers()
    t_ai_router_mock()
    t_normalize_content()
    print(f"\nKẾT QUẢ: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
