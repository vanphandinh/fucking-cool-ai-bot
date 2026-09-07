"""Handlers tin nhắn + lifecycle (tự rời group lạ)."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

from ..config import Settings
from ..core.context import ChatMemory
from ..core.formatting import clean_question, format_sources, split_plain
from ..core.orchestrator import AllProvidersFailed, Orchestrator
from ..core.rate_limiter import RateLimiter
from ..core.stats import Stats
from .filters import AllowedChat, TriggeredMessage

logger = logging.getLogger(__name__)

_HELP_TEXT = (
    "🤖 Mình là trợ lý AI của group (chạy bằng AI miễn phí + tìm kiếm web).\n\n"
    "Cách dùng:\n"
    "- Gõ @{bot} + câu hỏi, ví dụ: @{bot} giải thích blockchain là gì?\n"
    "- Hoặc reply vào tin của mình (hoặc tin của thành viên khác) và tag @{bot} để hỏi tiếp theo ngữ cảnh.\n"
    "- Lệnh: /ask <câu hỏi>, /help, /status (admin).\n\n"
    "Mẹo:\n"
    "- Hỏi tiếng Việt, mình trả lời tiếng Việt.\n"
    "- Cần tin mới (giá vàng, thời tiết, tin tức...) mình tự tìm web và đính nguồn.\n"
    "- Giới hạn {limit} câu/phút/người để tránh spam."
)


class _ChatLocks:
    """Khóa tuần tự theo chat — tránh 2 câu hỏi cùng lúc trong 1 group."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def get(self, chat_id: int) -> asyncio.Lock:
        async with self._guard:
            lock = self._locks.get(chat_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[chat_id] = lock
            return lock


def build_message_router(
    settings: Settings,
    orchestrator: Orchestrator,
    memory: ChatMemory,
    limiter: RateLimiter,
    stats: Stats,
) -> Router:
    router = Router()
    router.message.filter(AllowedChat(settings))
    chat_locks = _ChatLocks()

    @router.message(Command("help", "start"))
    async def help_handler(message: Message) -> None:
        await message.reply(
            _HELP_TEXT.format(bot=settings.bot_username, limit=settings.max_questions_per_min_per_user)
        )

    @router.message(Command("status"))
    async def status_handler(message: Message) -> None:
        if message.from_user and message.from_user.id not in settings.admin_ids_list:
            return  # im lặng với người không phải admin
        lines = [
            "📊 Trạng thái bot",
            f"- Uptime: {stats.uptime_text()}",
            f"- Câu hỏi: {stats.questions_total} (hôm nay {stats.questions_today})",
            f"- Số lần tìm web: {stats.searches}",
            f"- Provider hiện tại: {stats.last_provider or 'chưa có'}",
            f"- Phân bổ: " + (", ".join(f"{k}: {v}" for k, v in stats.by_provider.items()) or "chưa có"),
            f"- Fallback đã dùng: {stats.fallback_count}",
            f"- Lỗi gần nhất: {stats.last_error or 'không có'}",
            f"- Cấu hình AI: {', '.join(settings.configured_provider_names) or 'CHƯA CÓ KEY'}",
            f"- Search backend: {settings.search_backend}",
        ]
        await message.reply("\n".join(lines))

    @router.message(Command("ask"))
    async def ask_command(message: Message) -> None:
        question = _strip_command_args(message.text or "", "ask")
        if not question:
            await message.reply(
                f"Bạn muốn hỏi gì? Gõ: /ask <câu hỏi> — ví dụ: /ask Vì sao bầu trời xanh?"
            )
            return
        await _handle_question(message, question, quoted=None, settings=settings,
                               orchestrator=orchestrator, memory=memory,
                               limiter=limiter, stats=stats, chat_locks=chat_locks)

    @router.message(TriggeredMessage(settings))
    async def triggered_message(message: Message) -> None:
        text = message.text or ""
        quoted = None
        replied = message.reply_to_message
        if replied:
            quoted = replied.text or replied.caption or ""
        question = clean_question(text, settings.bot_username_clean)
        if not question:
            await message.reply("Mình đây! Bạn muốn hỏi gì? 🤔")
            return
        await _handle_question(message, question, quoted=quoted, settings=settings,
                               orchestrator=orchestrator, memory=memory,
                               limiter=limiter, stats=stats, chat_locks=chat_locks)

    return router


def build_lifecycle_router(settings: Settings) -> Router:
    """Xử lý sự kiện bot bị thêm vào chat -> tự rời group không thuộc allowlist."""
    router = Router()

    @router.chat_member()
    async def on_bot_added(update: ChatMemberUpdated, bot: Bot) -> None:
        if update.chat.type not in ("group", "supergroup"):
            return
        new_status = update.new_chat_member.status
        if new_status not in ("member", "administrator", "restricted"):
            return  # không phải sự kiện bot được thêm/trở thành thành viên

        chat_id = update.chat.id
        title = update.chat.title or "(không tên)"
        if chat_id in settings.allowed_group_ids_list:
            logger.info("Bot được thêm vào group allowlist: id=%s title=%s", chat_id, title)
            return

        if settings.learn_group_id_mode:
            logger.warning(
                "GROUP_ID_LEARN: chat_id=%s title=%s username=%s — KHÔNG rời group vì "
                "LEARN_GROUP_ID_MODE=1. Hãy điền chat_id này vào ALLOWED_GROUP_IDS.",
                chat_id,
                title,
                update.chat.username or "-",
            )
            return

        try:
            await bot.leave_chat(chat_id)
            logger.info("Đã tự rời group KHÔNG thuộc allowlist: id=%s title=%s", chat_id, title)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Không tự rời group %s được: %s", chat_id, exc)

    return router


async def _handle_question(
    message: Message,
    question: str,
    quoted: str | None,
    settings: Settings,
    orchestrator: Orchestrator,
    memory: ChatMemory,
    limiter: RateLimiter,
    stats: Stats,
    chat_locks: _ChatLocks,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    bot: Bot = message.bot

    allowed, _wait = limiter.allow(user_id)
    if not allowed:
        if limiter.should_warn(user_id):
            await message.reply("⏳ Bạn đang hỏi hơi nhanh, xin chờ một lát rồi thử lại nhé.")
        return

    stats.record_question()

    lock = await chat_locks.get(chat_id)
    async with lock:
        typing_task = asyncio.create_task(_typing_loop(bot, chat_id))
        try:
            history = memory.history_for(chat_id, settings.max_context_turns)
            answer = await orchestrator.ask(question=question, history=history, quoted=quoted)
        except AllProvidersFailed as exc:
            stats.record_error(str(exc), fallback=True)
            logger.error("Tất cả AI provider thất bại: %s", exc)
            await message.reply(
                "❌ Xin lỗi, hiện tại mình không thể trả lời (các nguồn AI đều đang lỗi/quá tải). "
                "Bạn thử lại sau vài phút nhé."
            )
            return
        except Exception:  # noqa: BLE001
            logger.exception("Lỗi không lường trước khi xử lý câu hỏi")
            await message.reply("❌ Có lỗi bất ngờ xảy ra. Bạn thử lại câu hỏi nhé!")
            return
        finally:
            typing_task.cancel()

        if not answer.text:
            await message.reply("❌ Mình không tạo được câu trả lời, thử lại nhé.")
            return

        stats.record_answer(answer.provider)
        if answer.searched:
            stats.record_search()

        memory.push(chat_id, "user", question)
        memory.push(chat_id, "assistant", answer.text)

        # Ghép phần trả lời + nguồn rồi gửi (cắt nếu quá dài)
        parts = split_plain(answer.text, 3900)
        if answer.searched and answer.sources:
            footer = format_sources(answer.sources)
            if footer:
                parts = parts + split_plain("\n\n" + footer, 3900)
        if not parts:
            parts = ["..."]
        try:
            first = parts[0]
            await message.reply(first)
            for part in parts[1:]:
                await bot.send_message(chat_id=chat_id, text=part)
                await asyncio.sleep(0.15)
        except Exception:  # noqa: BLE001
            logger.exception("Gửi câu trả lời thất bại (chat %s)", chat_id)


def _strip_command_args(text: str, command_name: str) -> str:
    """Lấy phần sau /ask (hỗ trợ /ask@Bot)."""
    body = text
    rest = ""
    if " " in body:
        body, rest = body.split(" ", 1)
    token = body[1:]  # bỏ dấu '/'
    if "@" in token:
        token = token.split("@", 1)[0]
    if token.lower() != command_name.lower():
        return ""
    return (rest or "").strip()


async def _typing_loop(bot: Bot, chat_id: int) -> None:
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(4.0)
