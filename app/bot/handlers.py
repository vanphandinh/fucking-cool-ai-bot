"""Handlers tin nhắn + lifecycle (tự rời group lạ)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberUpdated, LinkPreviewOptions, Message

from ..ai.base import AllProvidersFailed, NoCapableProvider
from ..config import Settings
from ..core.context import ChatMemory
from ..core.formatting import clean_question, format_sources, split_plain
from ..core.orchestrator import Orchestrator
from ..core.rate_limiter import RateLimiter
from ..core.request import UserRequest
from ..core.stats import Stats
from .filters import AllowedChat, TriggeredMessage
from .media import (
    ImageTooLarge,
    MediaValidationError,
    TelegramMediaLoader,
    UnsupportedImageFormat,
)

logger = logging.getLogger(__name__)

_HELP_TEXT = (
    "🤖 Mình là trợ lý AI của group (AI miễn phí + tìm kiếm web + đọc ảnh).\n\n"
    "Cách dùng:\n"
    "- Gõ @{bot} + câu hỏi.\n"
    "- Gửi JPEG/PNG/WebP kèm caption có @{bot}.\n"
    "- Reply ảnh rồi tag @{bot}, hoặc dùng /ask <câu hỏi>.\n"
    "- Lệnh: /ask, /help, /status (admin).\n\n"
    "Giới hạn {limit} câu/phút/người để tránh spam."
)


class _ChatLocks:
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
    media_loader = TelegramMediaLoader(settings)

    @router.message(Command("help", "start", ignore_case=True))
    async def help_handler(message: Message) -> None:
        await message.reply(
            _HELP_TEXT.format(bot=settings.bot_username, limit=settings.max_questions_per_min_per_user)
        )

    @router.message(Command("status", ignore_case=True))
    async def status_handler(message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        if uid is None or uid not in settings.admin_ids_list:
            return
        allowed_text = settings.allowed_group_ids_list or "TRỐNG"
        cooling = []
        for provider in orchestrator.router.providers:
            if not provider.health.available():
                state = "disabled" if provider.health.disabled else f"{provider.health.cooldown_seconds()}s"
                cooling.append(f"{provider.name}={state}")
        lines = [
            "📊 Trạng thái bot",
            f"- Chat hiện tại: {message.chat.id} (cho phép: {allowed_text})",
            f"- Uptime: {stats.uptime_text()}",
            f"- Câu hỏi: {stats.questions_total} (hôm nay {stats.live_questions_today()})",
            f"- Số lần tìm web: {stats.searches}",
            f"- Provider hiện tại: {stats.last_provider or 'chưa có'}",
            "- Phân bổ: " + (", ".join(f"{k}: {v}" for k, v in stats.by_provider.items()) or "chưa có"),
            f"- Fallback đã dùng: {stats.fallback_count}",
            f"- Lỗi gần nhất: {stats.last_error or 'không có'}",
            f"- Text providers: {', '.join(settings.configured_provider_names) or 'CHƯA CÓ KEY'}",
            f"- Vision providers: {', '.join(settings.configured_vision_provider_names) or 'disabled'}",
            f"- Vision enabled: {'yes' if settings.configured_vision_provider_names else 'no'}",
            f"- Cooldown/unavailable: {', '.join(cooling) or 'không có'}",
            f"- Search backend: {settings.search_backend}",
        ]
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
        await _handle_question(
            message,
            question,
            quoted=quoted,
            settings=settings,
            orchestrator=orchestrator,
            memory=memory,
            limiter=limiter,
            stats=stats,
            chat_locks=chat_locks,
            media_loader=media_loader,
        )

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
        await _handle_question(
            message,
            question,
            quoted=quoted,
            settings=settings,
            orchestrator=orchestrator,
            memory=memory,
            limiter=limiter,
            stats=stats,
            chat_locks=chat_locks,
            media_loader=media_loader,
        )

    return router


def build_lifecycle_router(settings: Settings) -> Router:
    router = Router()

    @router.my_chat_member()
    async def on_bot_added(update: ChatMemberUpdated, bot: Bot) -> None:
        if update.chat.type not in ("group", "supergroup", "channel"):
            return
        active = ("member", "administrator", "restricted")
        if update.new_chat_member.status not in active or update.old_chat_member.status in active:
            return
        chat_id = update.chat.id
        title = update.chat.title or "(không tên)"
        if chat_id in settings.allowed_group_ids_list:
            logger.info("Bot được thêm vào chat allowlist: id=%s title=%s", chat_id, title)
            return
        if settings.learn_group_id_mode:
            logger.warning(
                "GROUP_ID_LEARN: chat_id=%s title=%s username=%s type=%s — KHÔNG rời chat vì LEARN_GROUP_ID_MODE=1.",
                chat_id,
                title,
                update.chat.username or "-",
                update.chat.type,
            )
            return
        try:
            await bot.leave_chat(chat_id)
            logger.info("Đã tự rời chat KHÔNG thuộc allowlist: id=%s title=%s", chat_id, title)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Không tự rời chat %s được: %s", chat_id, exc)

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
    media_loader: TelegramMediaLoader,
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

    try:
        async with asyncio.timeout(settings.question_timeout_sec):
            lock = await chat_locks.get(chat_id)
            async with lock:
                try:
                    images = await media_loader.load(message)
                except UnsupportedImageFormat:
                    await message.reply("Hiện mình chỉ đọc JPEG, PNG hoặc WebP.")
                    return
                except ImageTooLarge:
                    await message.reply("Ảnh quá lớn để phân tích.")
                    return
                except MediaValidationError:
                    await message.reply("Không tải/đọc được ảnh này. Bạn thử gửi lại nhé.")
                    return

                request = UserRequest(text=question, quoted_text=quoted, images=images)
                typing_task = asyncio.create_task(
                    _typing_loop(bot, chat_id, message_thread_id=message.message_thread_id)
                )
                try:
                    history = memory.history_for(chat_id, settings.max_context_turns)
                    answer = await orchestrator.ask(request=request, history=history)
                except NoCapableProvider as exc:
                    stats.record_error(str(exc))
                    await message.reply("Hiện chưa có model đọc ảnh được cấu hình.")
                    return
                except AllProvidersFailed as exc:
                    stats.record_error(str(exc), fallback=True)
                    logger.error("Tất cả AI provider thất bại: %s", exc)
                    await message.reply(
                        "❌ Xin lỗi, hiện tại mình không thể trả lời (các nguồn AI đều đang lỗi/quá tải). Bạn thử lại sau vài phút nhé."
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    stats.record_error(str(exc))
                    logger.exception("Lỗi không lường trước khi xử lý câu hỏi")
                    await message.reply("❌ Có lỗi bất ngờ xảy ra. Bạn thử lại câu hỏi nhé!")
                    return
                finally:
                    typing_task.cancel()
                    await asyncio.gather(typing_task, return_exceptions=True)

                if not answer.text:
                    await message.reply("❌ Mình không tạo được câu trả lời, thử lại nhé.")
                    return

                parts = split_plain(answer.text, 3900) or ["..."]
                try:
                    await message.reply(parts[0])
                except Exception:  # noqa: BLE001
                    logger.exception("Gửi câu trả lời thất bại (chat %s)", chat_id)
                    return

                stats.record_answer(answer.provider)
                stats.record_fallback(answer.fallbacks)
                if answer.searched:
                    stats.record_search()
                memory_text = f"[kèm {len(images)} ảnh] {question}" if images else question
                memory.push(chat_id, "user", memory_text)
                memory.push(chat_id, "assistant", answer.text)

                extra_kwargs = {}
                if message.message_thread_id:
                    extra_kwargs["message_thread_id"] = message.message_thread_id
                try:
                    for part in parts[1:]:
                        await bot.send_message(chat_id=chat_id, text=part, **extra_kwargs)
                        await asyncio.sleep(0.15)
                    if answer.searched and answer.sources:
                        footer = format_sources(answer.sources)
                        if footer:
                            await bot.send_message(
                                chat_id=chat_id,
                                text=footer,
                                parse_mode="HTML",
                                link_preview_options=LinkPreviewOptions(is_disabled=True),
                                **extra_kwargs,
                            )
                except Exception:  # noqa: BLE001
                    logger.exception("Gửi phần tiếp theo thất bại (chat %s)", chat_id)
    except TimeoutError:
        stats.record_error("Câu hỏi quá thời gian xử lý")
        logger.warning("Câu hỏi quá thời gian xử lý (chat %s)", chat_id)
        await message.reply("⏳ Câu hỏi đã quá thời gian chờ/xử lý. Bạn thử lại sau nhé.")


async def _typing_loop(bot: Bot, chat_id: int, message_thread_id: int | None = None) -> None:
    kwargs: dict = {}
    if message_thread_id:
        kwargs["message_thread_id"] = message_thread_id
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action="typing", **kwargs)
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(4.0)
