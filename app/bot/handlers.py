"""Handlers tin nhắn + lifecycle (tự rời group lạ)."""

from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberUpdated, LinkPreviewOptions, Message

from ..ai.base import AllProvidersFailed, NoCapableProvider
from ..config import Settings
from ..core.context import ChatMemory
from ..core.formatting import clean_question, format_sources
from ..core.orchestrator import Orchestrator
from ..core.rate_limiter import RateLimiter
from ..core.request import UserRequest
from ..core.stats import Stats
from ..core.telegram_formatting import split_telegram_html, telegram_html_to_plain
from .filters import AllowedChat, TriggeredMessage
from .image_results import send_image_results
from .media import (
    ImageTooLarge,
    MediaValidationError,
    TelegramMediaLoader,
    UnsupportedImageFormat,
)

logger = logging.getLogger(__name__)

_HELP_TEXT = (
    "🤖 Mình là trợ lý AI của group (B.AI + tìm kiếm web/ảnh + đọc ảnh).\n\n"
    "Cách dùng:\n"
    "- Gõ @{bot} + câu hỏi.\n"
    "- Có thể yêu cầu tìm/xem hình ảnh từ Internet.\n"
    "- Gửi 1 ảnh JPEG/PNG/WebP kèm caption có @{bot}.\n"
    "- Reply ảnh rồi tag @{bot}, hoặc dùng /ask <câu hỏi>.\n"
    "- Lệnh: /ask, /help, /status (admin).\n\n"
    "Giới hạn {limit} câu/phút/người để tránh spam."
)


def _conversation_key(chat_id: int, message_thread_id: int | None) -> int | tuple[int, int]:
    """Keep normal chats backward-compatible while isolating Telegram forum topics."""
    if message_thread_id is None:
        return chat_id
    return chat_id, message_thread_id


class _ChatLocks:
    def __init__(self) -> None:
        self._locks: dict[int | tuple[int, int], asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def get(self, chat_id: int, message_thread_id: int | None = None) -> asyncio.Lock:
        key = _conversation_key(chat_id, message_thread_id)
        async with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[key] = lock
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
        cooling = []
        provider_router = getattr(orchestrator, "router", None)
        for provider in getattr(provider_router, "providers", []):
            health = getattr(provider, "health", None)
            if health is not None and not health.available():
                state = "disabled" if health.disabled else f"{health.cooldown_seconds()}s"
                cooling.append(f"{provider.name}={state}")
        distribution = (
            ", ".join(f"{k}: {v}" for k, v in stats.by_provider.items())
            or "chưa có"
        )
        vision_names = ", ".join(settings.configured_vision_provider_names) or "disabled"
        lines = [
            "📊 Trạng thái bot",
            f"- Chat hiện tại: {message.chat.id} (cho phép: {allowed_text})",
            f"- Uptime: {stats.uptime_text()}",
            f"- Câu hỏi: {stats.questions_total} (hôm nay {stats.live_questions_today()})",
            f"- Số lần tìm web/ảnh: {stats.searches}",
            f"- Provider hiện tại: {stats.last_provider or 'chưa có'}",
            f"- Phân bổ: {distribution}",
            f"- Lỗi gần nhất: {stats.last_error or 'không có'}",
            (
                "- B.AI text: "
                + (", ".join(settings.configured_provider_names) or "CHƯA CÓ KEY")
            ),
            f"- B.AI vision: {vision_names}",
            f"- Vision enabled: {'yes' if settings.configured_vision_provider_names else 'no'}",
            f"- Cooldown/unavailable: {', '.join(cooling) or 'không có'}",
            f"- Search backend (web + ảnh): {settings.search_backend}",
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
                "GROUP_ID_LEARN: chat_id=%s title=%s username=%s type=%s — "
                "KHÔNG rời chat vì LEARN_GROUP_ID_MODE=1.",
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


def _is_html_parse_error(exc: TelegramBadRequest) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in (
            "can't parse entities",
            "cant parse entities",
            "can't find end tag",
            "unsupported start tag",
            "entity",
        )
    )


def _delivery_payload(text: str) -> tuple[str, bool]:
    """Return Telegram payload plus whether HTML parsing is actually needed."""
    plain = telegram_html_to_plain(text)
    if html.escape(plain, quote=False) == text:
        return plain, False
    return text, True


async def _send_answer_parts(message: Message, parts: list[str]) -> bool:
    if not parts:
        return False

    preview = LinkPreviewOptions(is_disabled=True)

    async def send_first(text: str) -> None:
        payload, needs_html = _delivery_payload(text)
        kwargs = {"link_preview_options": preview}
        if needs_html:
            kwargs["parse_mode"] = "HTML"
        try:
            await message.reply(payload, **kwargs)
        except TelegramBadRequest as exc:
            if not needs_html or not _is_html_parse_error(exc):
                raise
            await message.reply(telegram_html_to_plain(text))

    try:
        await send_first(parts[0])
    except Exception:  # noqa: BLE001
        logger.exception("Gửi câu trả lời đầu tiên thất bại (chat %s)", message.chat.id)
        return False

    extra_kwargs = {}
    if message.message_thread_id:
        extra_kwargs["message_thread_id"] = message.message_thread_id

    try:
        for part in parts[1:]:
            payload, needs_html = _delivery_payload(part)
            send_kwargs = {
                "chat_id": message.chat.id,
                "text": payload,
                "link_preview_options": preview,
                **extra_kwargs,
            }
            if needs_html:
                send_kwargs["parse_mode"] = "HTML"
            try:
                await message.bot.send_message(**send_kwargs)
            except TelegramBadRequest as exc:
                if not needs_html or not _is_html_parse_error(exc):
                    raise
                await message.bot.send_message(
                    chat_id=message.chat.id,
                    text=telegram_html_to_plain(part),
                    **extra_kwargs,
                )
            await asyncio.sleep(0.15)
    except Exception:  # noqa: BLE001
        logger.exception("Gửi phần tiếp theo thất bại (chat %s)", message.chat.id)
        return False
    return True


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
    media_loader: TelegramMediaLoader | None = None,
) -> None:
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    message_thread_id = message.message_thread_id
    conversation_key = _conversation_key(chat_id, message_thread_id)
    bot: Bot = message.bot
    active_media_loader = media_loader or TelegramMediaLoader(settings)

    allowed, _wait = limiter.allow(user_id)
    if not allowed:
        if limiter.should_warn(user_id):
            await message.reply("⏳ Bạn đang hỏi hơi nhanh, xin chờ một lát rồi thử lại nhé.")
        return
    stats.record_question()

    try:
        async with asyncio.timeout(settings.question_timeout_sec):
            lock = await chat_locks.get(chat_id, message_thread_id)
            async with lock:
                try:
                    images = await active_media_loader.load(message)
                except UnsupportedImageFormat:
                    await message.reply("Hiện mình chỉ đọc JPEG, PNG hoặc WebP.")
                    return
                except ImageTooLarge:
                    await message.reply("Ảnh quá lớn để phân tích.")
                    return
                except MediaValidationError as exc:
                    if "Quá nhiều ảnh" in str(exc) and settings.configured_vision_provider_names:
                        await message.reply(
                            "Hiện B.AI chỉ hỗ trợ 1 ảnh mỗi yêu cầu. Bạn gửi từng ảnh nhé."
                        )
                    else:
                        await message.reply("Không tải/đọc được ảnh này. Bạn thử gửi lại nhé.")
                    return

                request = UserRequest(text=question, quoted_text=quoted, images=images)
                typing_task = asyncio.create_task(
                    _typing_loop(bot, chat_id, message_thread_id=message_thread_id)
                )
                try:
                    history = memory.history_for(conversation_key, settings.max_context_turns)
                    if images:
                        answer = await orchestrator.ask(request=request, history=history)
                    else:
                        answer = await orchestrator.ask(
                            question=question,
                            history=history,
                            quoted=quoted,
                        )
                except NoCapableProvider as exc:
                    stats.record_error(str(exc))
                    await message.reply(
                        "B.AI vision hiện chưa được cấu hình hoặc không hỗ trợ yêu cầu này."
                    )
                    return
                except AllProvidersFailed as exc:
                    stats.record_error(str(exc))
                    logger.error("B.AI không thể hoàn tất request: %s", exc)
                    await message.reply(
                        "❌ B.AI hiện đang lỗi/quá tải hoặc tạm thời không khả dụng. "
                        "Bạn thử lại sau vài phút nhé."
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

                parts = split_telegram_html(answer.text, 3900) or ["..."]
                if not await _send_answer_parts(message, parts):
                    return

                stats.record_answer(answer.provider)
                if answer.searched:
                    stats.record_search()
                memory_text = f"[kèm {len(images)} ảnh] {question}" if images else question
                memory.push(conversation_key, "user", memory_text)
                memory.push(conversation_key, "assistant", telegram_html_to_plain(answer.text))

                extra_kwargs = {}
                if message_thread_id:
                    extra_kwargs["message_thread_id"] = message_thread_id
                try:
                    if getattr(answer, "images", None):
                        await send_image_results(
                            bot,
                            chat_id,
                            answer.images,
                            message_thread_id=message_thread_id,
                        )
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
