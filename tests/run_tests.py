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

    from app.core.formatting import clean_question, format_sources, split_plain

    q = clean_question("@FuckingCoolAIbot  giá vàng hôm nay ?", "FuckingCoolAIbot")
    check("clean_question bỏ mention", "giá vàng hôm nay ?" in q)
    parts = split_plain("y" * 10000, 3900)
    check("split_plain giữ nguyên nội dung", "".join(parts) == "y" * 10000)
    check("split_plain không quá 3900", all(len(p) <= 3900 for p in parts))
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

    rl = RateLimiter(max_requests_per_min=2)
    check("rate limit allow 2", rl.allow(7)[0] and rl.allow(7)[0] and not rl.allow(7)[0])

    st = Stats()
    st.record_question()
    st.record_answer("gemini")
    check("stats ghi nhận", st.questions_total == 1 and st.last_provider == "gemini")


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


# ----------------------------------------------------------------------
def _msg(chat_id: int, chat_type: str, text: str,
         reply_to: "Message | None" = None) -> "Message":
    from aiogram.types import Chat, Message, User

    chat = Chat(id=chat_id, type=chat_type, title="G" if chat_type != "private" else None)
    sender = User(id=1, is_bot=False, first_name="A")
    return Message(
        message_id=10,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=sender,
        text=text,
        reply_to_message=reply_to,
    )


def _mk_reply(chat, text: str, username: str) -> "Message":
    from aiogram.types import Message, User

    bot_user = User(id=99, is_bot=True, first_name="Bot", username=username)
    return Message(
        message_id=5, date=datetime.now(timezone.utc), chat=chat,
        from_user=bot_user, text=text,
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
        check("mention kích hoạt", await trig(_msg(-100200, "supergroup", "giá @FuckingCoolAIbot?")))
        check("không trigger -> false", not await trig(_msg(-100200, "supergroup", "chào")))
        chat = _msg(-100200, "supergroup", "x").chat
        reply_bot = _mk_reply(chat, "câu trả lời", "FuckingCoolAIbot")
        check("reply tin bot kích hoạt",
              await trig(_msg(-100200, "supergroup", "tiếp theo?", reply_to=reply_bot)))
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
        Chat, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, Message, Update, User,
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

        # 2) mention câu hỏi -> trả lời, ghi memory
        session.calls.clear()
        up = mk_update(-100200, "supergroup", "giá vàng @FuckingCoolAIbot?", update_id=2)
        await dp.feed_update(bot, up)
        sent = [c for c in session.calls if c[0] == "SendMessage"]
        check("E2E: mention trả lời", any("TRẢ LỜI: giá vàng" in c[1].get("text", "") for c in sent))
        check("E2E: memory đã lưu câu hỏi",
              memory.history_for(-100200) and memory.history_for(-100200)[-1]["role"] == "assistant")

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
        check("E2E: reply tin bot đọc ngữ cảnh",
              any("đã đọc tin reply: nội dung cũ của bot" in t for t in reply_texts),
              str(reply_texts[:1]))

        # 6) /status của admin
        session.calls.clear()
        chat = Chat(id=-100200, type="supergroup", title="G")
        admin_msg = Message(
            message_id=20, date=datetime.now(timezone.utc), chat=chat,
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
        check("E2E: tự rời group lạ", len(leaves) == 1 and leaves[0][1].get("chat_id") == -777,
              str(leaves))

        # 8) lifecycle: bot thêm vào group ĐÚNG allowlist -> không rời
        session.calls.clear()
        upd2 = ChatMemberUpdated(
            chat=Chat(id=-100200, type="supergroup", title="Group Đúng"),
            from_user=User(id=9, is_bot=False, first_name="X"),
            date=datetime.now(timezone.utc),
            old_chat_member=ChatMemberLeft(
                user=User(id=99, is_bot=True, first_name="Bot")),
            new_chat_member=ChatMemberMember(
                user=User(id=99, is_bot=True, first_name="Bot")),
        )
        await dp.feed_update(bot, Update(update_id=8, my_chat_member=upd2))
        check("E2E: group đúng không bị rời",
              not any(c[0] == "LeaveChat" for c in session.calls), str(session.calls))

        await session.close()

    asyncio.run(run())


# ----------------------------------------------------------------------
def t_ai_router_mock():
    print("== AI router (mock OpenAI server) ==")
    STATE = {"gemini": 0, "groq": 0, "plain": 0}

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
                        {"error": {"message": "does not support function calling"}}, 400)
                STATE["plain"] += 1
                return self._send({"choices": [{"message": {"content": "plain answer"}}]})
            # model tool-user
            STATE["groq"] += 1
            last = req["messages"][-1]
            if last["role"] == "user":
                return self._send({
                    "choices": [{"message": {
                        "content": None,
                        "tool_calls": [{
                            "id": "c1", "type": "function",
                            "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                        }],
                    }}]
                })
            if last["role"] == "tool":
                check("mock: tool result truyền lại model",
                      "Kết quả" in last.get("content", ""), last.get("content", "")[:60])
                return self._send({"choices": [{"message": {"content": "FINAL"}}]})
            return self._send({"choices": [{"message": {"content": "other"}}]})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        from app.ai.base import AllProvidersFailed, OpenAICompatProvider
        from app.ai.router import AIProviderRouter

        base = f"http://127.0.0.1:{port}"
        tools = [{"type": "function", "function": {"name": "web_search",
                                                   "parameters": {"type": "object"}}}]

        async def ex(name, args):
            return "Kết quả tìm kiếm: 1. A"

        async def run():
            # fallback 429 -> tool-loop -> FINAL
            r1 = AIProviderRouter([
                OpenAICompatProvider("g", base, "k", "gemini-429", timeout=5),
                OpenAICompatProvider("groq", base, "k", "tool-user", timeout=5),
            ])
            text, prov = await r1.complete([{"role": "user", "content": "hi"}], tools, ex)
            check("fallback 429->groq + tool loop", text == "FINAL" and prov == "groq",
                  f"{text}/{prov}")

            # provider không hỗ trợ tools -> retry plain
            r2 = AIProviderRouter([
                OpenAICompatProvider("nt", base, "k", "no-tools", timeout=5)])
            text2, prov2 = await r2.complete([{"role": "user", "content": "hi"}], tools, ex)
            check("retry không tools", text2 == "plain answer")

            # tất cả 429 -> AllProvidersFailed
            r3 = AIProviderRouter([
                OpenAICompatProvider("g", base, "k", "gemini-429", timeout=5)])
            try:
                await r3.complete([{"role": "user", "content": "hi"}], tools, ex)
                check("AllProvidersFailed ném", False)
            except AllProvidersFailed:
                check("AllProvidersFailed ném", True)

        asyncio.run(run())
    finally:
        server.shutdown()


if __name__ == "__main__":
    t_config_formatting()
    t_core()
    t_reader_guard()
    t_filters()
    t_e2e_handlers()
    t_ai_router_mock()
    print(f"\nKẾT QUẢ: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
