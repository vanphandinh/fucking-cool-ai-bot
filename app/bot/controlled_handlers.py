"""Message handlers used only when renewable question controls are enabled."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..core.formatting import clean_question
from .filters import AllowedChat, TriggeredMessage
from .handlers import _provider_status_lines
from .media import TelegramMediaLoader
from .question_runner import submit_controlled_question

_HELP_TEXT = (
    "🤖 Mình là trợ lý AI của group (AI + tìm kiếm web/ảnh + đọc ảnh).\n\n"
    "Cách dùng:\n"
    "- Gõ @{bot} + câu hỏi, hoặc /ask <câu hỏi>.\n"
    "- Có thể gửi/reply ảnh JPEG, PNG hoặc WebP.\n"
    "- Với tác vụ dài, bot cập nhật tiến độ thật và sẽ hỏi Tiếp tục/Dừng thay vì "
    "tự hủy toàn bộ câu hỏi.\n"
    "- Lệnh: /ask, /help, /status (admin).\n\n"
    "Giới hạn {limit} câu/phút/người để tránh spam."
)


def build_controlled_message_router(
    settings,
    orchestrator,
    memory,
    limiter,
    stats,
    manager,
    presenter,
) -> Router:
    router = Router()
    router.message.filter(AllowedChat(settings))
    media_loader = TelegramMediaLoader(settings)

    async def submit(message: Message, question: str, quoted: str | None) -> None:
        await submit_controlled_question(
            message,
            question,
            quoted,
            settings=settings,
            orchestrator=orchestrator,
            memory=memory,
            limiter=limiter,
            stats=stats,
            media_loader=media_loader,
            manager=manager,
            presenter=presenter,
        )

    @router.message(Command("help", "start", ignore_case=True))
    async def help_handler(message: Message) -> None:
        await message.reply(
            _HELP_TEXT.format(
                bot=settings.bot_username,
                limit=settings.max_questions_per_min_per_user,
            )
        )

    @router.message(Command("status", ignore_case=True))
    async def status_handler(message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        if uid is None or uid not in settings.admin_ids_list:
            return
        allowed_text = settings.allowed_group_ids_list or "TRỐNG"
        lines = [
            "📊 Trạng thái bot",
            f"- Chat hiện tại: {message.chat.id} (cho phép: {allowed_text})",
            f"- Uptime: {stats.uptime_text()}",
            f"- Câu hỏi: {stats.questions_total} (hôm nay {stats.live_questions_today()})",
            f"- Số lần tìm web/ảnh: {stats.searches}",
            f"- Lỗi gần nhất: {stats.last_error or 'không có'}",
            "- Question mode: renewable controls",
        ]
        lines.extend(f"- {line}" for line in _provider_status_lines(orchestrator.router, stats))
        lines.append(f"- Search backend (web + ảnh): {settings.search_backend}")
        await message.reply("\n".join(lines))

    @router.message(Command("ask", ignore_case=True))
    async def ask_command(message: Message, command: CommandObject) -> None:
        question = (command.args or "").strip()
        if not question:
            await message.reply("Bạn muốn hỏi gì? Gõ: /ask <câu hỏi>.")
            return
        quoted = None
        if message.reply_to_message:
            quoted = message.reply_to_message.text or message.reply_to_message.caption or ""
        await submit(message, question, quoted)

    @router.message(TriggeredMessage(settings))
    async def triggered_message(message: Message) -> None:
        text = message.text or message.caption or ""
        quoted = None
        if message.reply_to_message:
            quoted = message.reply_to_message.text or message.reply_to_message.caption or ""
        question = clean_question(text, settings.bot_username_clean)
        if not question:
            await message.reply("Mình đây! Bạn muốn hỏi gì? 🤔")
            return
        await submit(message, question, quoted)

    return router
