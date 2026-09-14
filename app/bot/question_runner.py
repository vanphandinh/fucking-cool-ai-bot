"""Bot-layer adapter between Telegram messages and core renewable jobs."""
from __future__ import annotations

import asyncio
import html
import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import LinkPreviewOptions, ReplyParameters

from ..ai.base import AllProvidersFailed, NoCapableProvider
from ..core.context import ChatMemory
from ..core.formatting import format_sources
from ..core.job_manager import JobCapacityError, JobSubmission, UserFacingJobError
from ..core.request import UserRequest
from ..core.telegram_formatting import split_telegram_html, telegram_html_to_plain
from .image_results import send_image_results
from .media import (
    ImageTooLarge,
    MediaValidationError,
    UnsupportedImageFormat,
)

logger = logging.getLogger(__name__)

_ALL_PROVIDERS_FAILED_TEXT = (
    "Các AI provider hiện đang lỗi/quá tải hoặc tạm thời không khả dụng. "
    "Bạn thử lại sau vài phút nhé."
)
_NO_CAPABLE_PROVIDER_TEXT = "Không có AI provider nào đang cấu hình hỗ trợ loại input này."


def conversation_key(chat_id: int, topic_id: int | None) -> int | tuple[int, int]:
    return chat_id if topic_id is None else (chat_id, topic_id)


def _vision_limit(settings, provider_router) -> int:
    provider_limit = provider_router.max_supported_images()
    if provider_limit <= 0:
        return 0
    return min(settings.max_images_per_request, provider_limit)


def _vision_limit_message(settings, provider_router) -> str:
    limit = _vision_limit(settings, provider_router)
    if limit <= 0:
        return _NO_CAPABLE_PROVIDER_TEXT
    return (
        "Các AI provider vision hiện đang cấu hình hỗ trợ tối đa "
        f"{limit} ảnh mỗi yêu cầu. Bạn gửi ít ảnh hơn nhé."
    )


def _delivery_payload(text: str) -> tuple[str, bool]:
    plain = telegram_html_to_plain(text)
    if html.escape(plain, quote=False) == text:
        return plain, False
    return text, True


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


class QuestionProcessor:
    def __init__(self, bot, settings, orchestrator, memory: ChatMemory, stats) -> None:
        self.bot = bot
        self.settings = settings
        self.orchestrator = orchestrator
        self.memory = memory
        self.stats = stats

    async def execute(self, record, operations):
        request = record.prepared_request
        if request is None:
            request = UserRequest(
                text=record.submission.question,
                quoted_text=record.submission.quoted,
            )
        try:
            answer = await self.orchestrator.ask(
                request=request,
                history=record.submission.history,
                operations=operations,
            )
        except NoCapableProvider as exc:
            self.stats.record_error(str(exc))
            raise UserFacingJobError(_NO_CAPABLE_PROVIDER_TEXT) from exc
        except AllProvidersFailed as exc:
            self.stats.record_fallbacks(exc.fallbacks)
            self.stats.record_error(str(exc))
            raise UserFacingJobError(_ALL_PROVIDERS_FAILED_TEXT) from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - convert to safe UI error at job boundary
            self.stats.record_error(str(exc))
            logger.exception("Lỗi không lường trước khi xử lý controlled question")
            raise UserFacingJobError(
                "Có lỗi bất ngờ xảy ra. Bạn thử lại câu hỏi nhé."
            ) from exc
        if not answer.text:
            raise UserFacingJobError("Mình không tạo được câu trả lời, bạn thử lại nhé.")
        return answer

    async def deliver(self, record, answer) -> None:
        parts = split_telegram_html(answer.text, 3900) or ["..."]
        await self._send_answer_parts(record, parts)

        self.stats.record_fallbacks(answer.fallbacks)
        self.stats.record_answer(answer.provider)
        if answer.searched:
            self.stats.record_search()

        request = record.prepared_request
        image_count = len(request.images) if request is not None else 0
        memory_text = (
            f"[kèm {image_count} ảnh] {record.submission.question}"
            if image_count
            else record.submission.question
        )
        key = conversation_key(record.submission.chat_id, record.submission.topic_id)
        self.memory.push_exchange(
            key,
            memory_text,
            telegram_html_to_plain(answer.text),
        )

        try:
            if getattr(answer, "images", None):
                await send_image_results(
                    self.bot,
                    record.submission.chat_id,
                    answer.images,
                    message_thread_id=record.submission.topic_id,
                )
            if answer.searched and answer.sources:
                footer = format_sources(answer.sources)
                if footer:
                    kwargs = {}
                    if record.submission.topic_id is not None:
                        kwargs["message_thread_id"] = record.submission.topic_id
                    await self.bot.send_message(
                        chat_id=record.submission.chat_id,
                        text=footer,
                        parse_mode="HTML",
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                        **kwargs,
                    )
        except Exception:  # noqa: BLE001 - extras must not turn delivered answer into AI failure
            logger.exception(
                "Gửi sources/images của controlled question thất bại (chat %s)",
                record.submission.chat_id,
            )

    async def _send_answer_parts(self, record, parts: list[str]) -> None:
        preview = LinkPreviewOptions(is_disabled=True)
        for index, part in enumerate(parts):
            payload, needs_html = _delivery_payload(part)
            kwargs = {
                "chat_id": record.submission.chat_id,
                "text": payload,
                "link_preview_options": preview,
            }
            if record.submission.topic_id is not None:
                kwargs["message_thread_id"] = record.submission.topic_id
            if index == 0:
                kwargs["reply_parameters"] = ReplyParameters(
                    message_id=record.submission.request_message_id
                )
            if needs_html:
                kwargs["parse_mode"] = "HTML"
            try:
                await self.bot.send_message(**kwargs)
            except TelegramBadRequest as exc:
                if not needs_html or not _is_html_parse_error(exc):
                    raise
                kwargs.pop("parse_mode", None)
                kwargs["text"] = telegram_html_to_plain(part)
                await self.bot.send_message(**kwargs)
            if index + 1 < len(parts):
                await asyncio.sleep(0.15)


async def submit_controlled_question(
    message,
    question: str,
    quoted: str | None,
    *,
    settings,
    orchestrator,
    memory,
    limiter,
    stats,
    media_loader,
    manager,
    presenter,
) -> str | None:
    user_id = message.from_user.id if message.from_user else 0
    chat_id = message.chat.id
    topic_id = message.message_thread_id

    allowed, _wait = limiter.allow(user_id)
    if not allowed:
        if limiter.should_warn(user_id):
            await message.reply("⏳ Bạn đang hỏi hơi nhanh, xin chờ một lát rồi thử lại nhé.")
        return None

    key = conversation_key(chat_id, topic_id)
    history = memory.history_for(key, settings.max_context_turns)
    status = await message.reply("⏳ Đang chờ lượt xử lý...")

    async def prepare(operations):
        try:
            images = await operations.run(
                "Tải ảnh Telegram",
                lambda: media_loader.load(message),
                timeout_sec=settings.tool_call_total_timeout_sec,
            )
        except UnsupportedImageFormat as exc:
            raise UserFacingJobError("Hiện mình chỉ đọc JPEG, PNG hoặc WebP.") from exc
        except ImageTooLarge as exc:
            raise UserFacingJobError("Ảnh quá lớn để phân tích.") from exc
        except MediaValidationError as exc:
            if "Quá nhiều ảnh" in str(exc):
                text = _vision_limit_message(settings, orchestrator.router)
            else:
                text = "Không tải/đọc được ảnh này. Bạn thử gửi lại nhé."
            raise UserFacingJobError(text) from exc
        if images and len(images) > _vision_limit(settings, orchestrator.router):
            raise UserFacingJobError(_vision_limit_message(settings, orchestrator.router))
        return UserRequest(text=question, quoted_text=quoted, images=images)

    submission = JobSubmission(
        question=question,
        quoted=quoted,
        owner_id=user_id,
        chat_id=chat_id,
        topic_id=topic_id,
        request_message_id=message.message_id,
        status_message_id=status.message_id,
        history=history,
        prepare_request=prepare,
    )
    try:
        job_id = await manager.submit(submission)
    except JobCapacityError:
        try:
            await status.edit_text(
                "⏳ Hiện có quá nhiều tác vụ đang chờ. Bạn thử lại sau một chút nhé."
            )
        except Exception:  # noqa: BLE001
            pass
        return None

    record = manager.get(job_id)
    if record is not None and record.submission.status_message_id != status.message_id:
        # Telegram may redeliver the same update while the first submission is alive.
        # Manager admission is idempotent; remove the extra status and do not count a
        # second question or hold the duplicate Message via its prepare closure.
        try:
            await status.delete()
        except Exception:  # noqa: BLE001 - duplicate cleanup is best effort
            pass
        return job_id

    stats.record_question()
    presenter.enqueue(job_id, force=True)
    return job_id
