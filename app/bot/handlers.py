"""Handlers tin nhắn + lifecycle (tự rời group lạ)."""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatMemberUpdated, LinkPreviewOptions, Message

from ..ai.base import AllProvidersFailed, NoCapableProvider
from ..ai.recovery import HealthScope
from ..ai.router import AIProviderRouter
from ..ai.target import provider_target_identity
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
    "🤖 Mình là trợ lý AI của group (AI + tìm kiếm web/ảnh + đọc ảnh).\n\n"
    "Cách dùng:\n"
    "- Gõ @{bot} + câu hỏi.\n"
    "- Có thể yêu cầu tìm/xem hình ảnh từ Internet.\n"
    "- Gửi ảnh JPEG/PNG/WebP kèm caption có @{bot}.\n"
    "- Reply ảnh rồi tag @{bot}, hoặc dùng /ask <câu hỏi>.\n"
    "- Lệnh: /ask, /help, /status (admin).\n\n"
    "Giới hạn {limit} câu/phút/người để tránh spam."
)
_ALL_PROVIDERS_FAILED_TEXT = (
    "❌ Các AI provider hiện đang lỗi/quá tải hoặc tạm thời không khả dụng. "
    "Bạn thử lại sau vài phút nhé."
)
_NO_CAPABLE_PROVIDER_TEXT = (
    "Không có AI provider nào đang cấu hình hỗ trợ loại input này."
)
_INCOMPLETE_DELIVERY_TEXT = (
    "⚠️ Câu trả lời mới gửi được một phần do lỗi Telegram. "
    "Phần đã gửi được giữ lại; bạn có thể hỏi lại để tiếp tục."
)


def _provider_status_lines(
    provider_router: AIProviderRouter,
    stats: Stats,
) -> list[str]:
    text_names = ", ".join(provider_router.configured_provider_names(False)) or "disabled"
    vision_names = ", ".join(provider_router.configured_provider_names(True)) or "disabled"
    text_order = ", ".join(provider_router.provider_order(False)) or "disabled"
    vision_order = ", ".join(provider_router.provider_order(True)) or "disabled"
    distribution = (
        ", ".join(f"{key}: {value}" for key, value in stats.by_provider.items())
        or "chưa có"
    )
    unavailable: list[str] = []
    scoped_order = (
        HealthScope.FAMILY,
        HealthScope.CREDENTIAL,
        HealthScope.MODEL,
        HealthScope.ENTITLEMENT,
        HealthScope.TARGET,
    )
    for provider in provider_router.providers:
        identity = provider_target_identity(provider)
        scoped = [
            provider_router.scoped_health.health(scope, identity)
            for scope in scoped_order
        ]
        scoped_unavailable = [health for health in scoped if not health.available()]
        if scoped_unavailable:
            if any(health.disabled for health in scoped_unavailable):
                state = "scoped-disabled"
            else:
                cooldown = max(
                    (health.cooldown_seconds() for health in scoped_unavailable),
                    default=0,
                )
                state = f"scoped-cooldown:{cooldown}s"
            unavailable.append(f"{identity.target_id}={state}")
            continue

        health = provider.health
        if health.available():
            continue
        state = (
            "adapter-disabled"
            if health.disabled
            else f"adapter-cooldown:{health.cooldown_seconds()}s"
        )
        unavailable.append(f"{identity.target_id}={state}")
    return [
        f"Text providers: {text_names}",
        f"Vision providers: {vision_names}",
        f"Text order: {text_order}",
        f"Vision order: {vision_order}",
        f"Provider hiện tại: {stats.last_provider or 'chưa có'}",
        f"Phân bổ: {distribution}",
        f"Fallbacks: {stats.fallback_count}",
        (
            f"Target rotations: {stats.target_rotation_count} "
            f"(models={stats.model_rotation_count}, "
            f"credentials={stats.credential_failover_count})"
        ),
        f"Cooldown/unavailable: {', '.join(unavailable) or 'không có'}",
    ]


def _effective_vision_limit(
    settings: Settings,
    provider_router: AIProviderRouter,
) -> int:
    provider_limit = provider_router.max_supported_images()
    if provider_limit <= 0:
        return 0
    return min(settings.max_images_per_request, provider_limit)


def _vision_limit_message(
    settings: Settings,
    provider_router: AIProviderRouter,
) -> str:
    effective_limit = _effective_vision_limit(settings, provider_router)
    if effective_limit <= 0:
        return _NO_CAPABLE_PROVIDER_TEXT
    return (
        "Các AI provider vision hiện đang cấu hình hỗ trợ tối đa "
        f"{effective_limit} ảnh mỗi yêu cầu. Bạn gửi ít ảnh hơn nhé."
    )


def _conversation_key(chat_id: int, message_thread_id: int | None) -> int | tuple[int, int]:
    """Keep normal chats backward-compatible while isolating Telegram forum topics."""
    if message_thread_id is None:
        return chat_id
    return chat_id, message_thread_id


@dataclass
class LockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    references: int = 0


class _ChatLocks:
    def __init__(self) -> None:
        self._locks: dict[int | tuple[int, int], LockEntry] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def hold(
        self, chat_id: int, message_thread_id: int | None = None
    ) -> AsyncIterator[None]:
        key = _conversation_key(chat_id, message_thread_id)
        async with self._guard:
            entry = self._locks.get(key)
            if entry is None:
                entry = LockEntry()
                self._locks[key] = entry
            entry.references += 1
        try:
            async with entry.lock:
                yield
        finally:
            async with self._guard:
                entry.references -= 1
                if entry.references == 0 and self._locks.get(key) is entry:
                    del self._locks[key]


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
        lines = [
            "📊 Trạng thái bot",
            f"- Chat hiện tại: {message.chat.id} (cho phép: {allowed_text})",
            f"- Uptime: {stats.uptime_text()}",
            f"- Câu hỏi: {stats.questions_total} (hôm nay {stats.live_questions_today()})",
            f"- Số lần tìm web/ảnh: {stats.searches}",
            f"- Câu trả lời gửi chưa hoàn chỉnh: {stats.partial_deliveries}",
            f"- Lỗi gần nhất: {stats.last_error or 'không có'}",
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
            logger.warning(
                "Không tự rời chat %s được: exc_type=%s",
                chat_id,
                type(exc).__name__,
            )

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


async def _send_answer_parts(
    message: Message,
    parts: list[str],
    *,
    on_ack: Callable[[str], None] | None = None,
    on_all_acked: Callable[[], None] | None = None,
) -> bool:
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
        if on_ack is not None:
            on_ack(telegram_html_to_plain(parts[0]))
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Gửi câu trả lời đầu tiên thất bại (chat %s): exc_type=%s",
            message.chat.id,
            type(exc).__name__,
        )
        return False

    if len(parts) == 1 and on_all_acked is not None:
        on_all_acked()

    extra_kwargs = {}
    if message.message_thread_id:
        extra_kwargs["message_thread_id"] = message.message_thread_id

    try:
        for index, part in enumerate(parts[1:], start=1):
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
            if on_ack is not None:
                on_ack(telegram_html_to_plain(part))
            if index < len(parts) - 1:
                await asyncio.sleep(0.15)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Gửi phần tiếp theo thất bại (chat %s): exc_type=%s",
            message.chat.id,
            type(exc).__name__,
        )
        return False
    if on_all_acked is not None:
        on_all_acked()
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
    acknowledged_parts: list[str] = []
    partial_committed = False

    try:
        async with asyncio.timeout(settings.question_timeout_sec):
            async with chat_locks.hold(chat_id, message_thread_id):
                try:
                    images = await active_media_loader.load(message)
                except UnsupportedImageFormat:
                    await message.reply("Hiện mình chỉ đọc JPEG, PNG hoặc WebP.")
                    return
                except ImageTooLarge:
                    await message.reply("Ảnh quá lớn để phân tích.")
                    return
                except MediaValidationError as exc:
                    if "Quá nhiều ảnh" in str(exc):
                        await message.reply(
                            _vision_limit_message(settings, orchestrator.router)
                        )
                    else:
                        await message.reply("Không tải/đọc được ảnh này. Bạn thử gửi lại nhé.")
                    return

                if images and len(images) > _effective_vision_limit(
                    settings, orchestrator.router
                ):
                    await message.reply(_vision_limit_message(settings, orchestrator.router))
                    return

                request = UserRequest(
                    text=question, quoted_text=quoted, images=images
                )
                typing_task = asyncio.create_task(
                    _typing_loop(bot, chat_id, message_thread_id=message_thread_id)
                )
                try:
                    history = memory.history_for(conversation_key, settings.max_context_turns)
                    answer = await orchestrator.ask(request=request, history=history)
                except NoCapableProvider as exc:
                    stats.record_error(type(exc).__name__)
                    await message.reply(_NO_CAPABLE_PROVIDER_TEXT)
                    return
                except AllProvidersFailed as exc:
                    stats.record_fallbacks(exc.fallbacks)
                    stats.record_target_recovery(
                        target_rotations=getattr(exc, "target_rotations", 0),
                        model_rotations=getattr(exc, "model_rotations", 0),
                        credential_failovers=getattr(exc, "credential_failovers", 0),
                    )
                    stats.record_error(type(exc).__name__)
                    logger.error(
                        "AI providers không thể hoàn tất request: exc_type=%s",
                        type(exc).__name__,
                    )
                    await message.reply(_ALL_PROVIDERS_FAILED_TEXT)
                    return
                except Exception as exc:  # noqa: BLE001
                    stats.record_error(type(exc).__name__)
                    logger.error(
                        "Lỗi không lường trước khi xử lý câu hỏi: exc_type=%s",
                        type(exc).__name__,
                    )
                    await message.reply("❌ Có lỗi bất ngờ xảy ra. Bạn thử lại câu hỏi nhé!")
                    return
                finally:
                    typing_task.cancel()
                    await asyncio.gather(typing_task, return_exceptions=True)

                stats.record_fallbacks(answer.fallbacks)
                stats.record_target_recovery(
                    target_rotations=getattr(answer, "target_rotations", 0),
                    model_rotations=getattr(answer, "model_rotations", 0),
                    credential_failovers=getattr(answer, "credential_failovers", 0),
                )
                if not answer.text:
                    await message.reply("❌ Mình không tạo được câu trả lời, thử lại nhé.")
                    return

                parts = split_telegram_html(answer.text, 3900) or ["..."]
                full_committed = False

                def commit_full_delivery() -> None:
                    nonlocal full_committed
                    if full_committed:
                        return
                    full_committed = True
                    stats.record_answer(answer.provider)
                    if answer.searched:
                        stats.record_search()
                    memory_text = (
                        f"[kèm {len(images)} ảnh] {question}"
                        if images
                        else question
                    )
                    memory.push_exchange(
                        conversation_key,
                        memory_text,
                        telegram_html_to_plain(answer.text),
                    )

                def commit_partial_delivery() -> bool:
                    nonlocal partial_committed
                    if full_committed or partial_committed or not acknowledged_parts:
                        return False
                    partial_committed = True
                    marker = "\n[...] Câu trả lời chưa hoàn chỉnh"
                    prefix = "\n".join(acknowledged_parts)
                    incomplete_answer = prefix[: 4000 - len(marker)] + marker
                    memory_text = (
                        f"[kèm {len(images)} ảnh] {question}"
                        if images
                        else question
                    )
                    try:
                        memory.push_exchange(
                            conversation_key,
                            memory_text,
                            incomplete_answer,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            "Partial answer memory commit failed: exc_type=%s",
                            type(exc).__name__,
                        )
                    try:
                        stats.record_partial_delivery()
                    except Exception as exc:  # noqa: BLE001
                        logger.error(
                            "Partial answer accounting failed: exc_type=%s",
                            type(exc).__name__,
                        )
                    return True

                try:
                    delivered = await _send_answer_parts(
                        message,
                        parts,
                        on_ack=acknowledged_parts.append,
                        on_all_acked=commit_full_delivery,
                    )
                except asyncio.CancelledError:
                    commit_partial_delivery()
                    raise
                if not delivered:
                    if commit_partial_delivery():
                        await message.reply(_INCOMPLETE_DELIVERY_TEXT)
                    return

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
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Gửi phần tiếp theo thất bại (chat %s): exc_type=%s",
                        chat_id,
                        type(exc).__name__,
                    )
    except TimeoutError:
        stats.record_error("Câu hỏi quá thời gian xử lý")
        logger.warning("Câu hỏi quá thời gian xử lý (chat %s)", chat_id)
        timeout_message = (
            _INCOMPLETE_DELIVERY_TEXT
            if partial_committed
            else "⏳ Câu hỏi đã quá thời gian chờ/xử lý. Bạn thử lại sau nhé."
        )
        await message.reply(timeout_message)


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
