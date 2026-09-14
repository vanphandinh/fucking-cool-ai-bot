"""Coalesced Telegram status rendering for renewable question jobs."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .job_callbacks import make_callback_data

_TERMINAL = frozenset({"COMPLETED", "CANCELLED", "FAILED"})


def _duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes} phút {secs} giây"
    return f"{secs} giây"


def _renewal_text(seconds: float) -> str:
    total = max(1, int(round(seconds)))
    if total % 60 == 0:
        return f"{total // 60} phút"
    return f"{total} giây"


def _keyboard(snapshot) -> InlineKeyboardMarkup | None:
    if snapshot.state in _TERMINAL or snapshot.state == "DELIVERING":
        return None
    stop = InlineKeyboardButton(
        text="Dừng",
        callback_data=make_callback_data(snapshot.job_id, snapshot.generation, "s"),
    )
    if snapshot.state != "AWAITING_CONSENT":
        return InlineKeyboardMarkup(inline_keyboard=[[stop]])
    cont = InlineKeyboardButton(
        text="Tiếp tục",
        callback_data=make_callback_data(snapshot.job_id, snapshot.generation, "c"),
    )
    return InlineKeyboardMarkup(inline_keyboard=[[cont, stop]])


def render_job_status(
    snapshot,
    *,
    renewal_sec: float,
    stage_label: str | None = None,
    now: float | None = None,
) -> tuple[str, InlineKeyboardMarkup | None]:
    current = time.monotonic() if now is None else now
    elapsed = max(0.0, current - snapshot.created_at)
    ran = _duration(elapsed)

    if snapshot.state == "QUEUED":
        text = f"⏳ Đang chờ lượt xử lý.\nĐã chạy: {ran}."
    elif snapshot.state == "RUNNING":
        detail = f"\nBước hiện tại: {stage_label}." if stage_label else ""
        text = f"🤖 Đang xử lý.\nĐã chạy: {ran}.{detail}"
    elif snapshot.state == "AWAITING_CONSENT":
        tail = (
            "Đang hoàn tất bước hiện tại."
            if snapshot.active_operations > 0
            else "Đang chờ lựa chọn của bạn."
        )
        text = (
            "⏱️ Quá trình lâu hơn dự kiến.\n"
            f"Đã chạy: {ran}.\n"
            f"Bạn muốn tiếp tục thêm {_renewal_text(renewal_sec)} không?\n"
            f"{tail}"
        )
    elif snapshot.state == "DELIVERING":
        text = f"📤 Đang gửi kết quả.\nĐã chạy: {ran}."
    elif snapshot.state == "COMPLETED":
        text = f"✅ Hoàn tất.\nTổng thời gian: {ran}."
    elif snapshot.state == "CANCELLED":
        text = f"⛔ Đã dừng theo yêu cầu.\nTổng thời gian: {ran}."
    else:
        reason = snapshot.error or "Tác vụ không thể hoàn tất."
        text = f"❌ {reason}\nTổng thời gian: {ran}."
    return text, _keyboard(snapshot)


@dataclass
class _PresenterState:
    stage_label: str | None = None
    last_text: str | None = None
    last_edit: float = 0.0
    replacement_used: bool = False


class JobStatusPresenter:
    """Serialize edits per job and coalesce non-terminal progress updates."""

    def __init__(self, bot, settings, manager) -> None:
        self.bot = bot
        self.settings = settings
        self.manager = manager
        self._states: dict[str, _PresenterState] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()
        self._scheduled: dict[str, asyncio.Task] = {}
        self._closing = False

    def emit(self, record, event) -> None:
        if self._closing:
            return
        state = self._states.setdefault(record.job_id, _PresenterState())
        kind = getattr(event, "kind", None)
        label = getattr(event, "label", None)
        if kind == "stage_started" and label:
            state.stage_label = str(label)[:120]
        elif kind == "stage_finished" and label == state.stage_label:
            state.stage_label = None

        force = False
        if isinstance(event, tuple) and event:
            force = event[0] in {"state", "renewal_requested", "renewal_accepted"}
        self.enqueue(record.job_id, force=force)

    def enqueue(self, job_id: str, *, force: bool = False) -> None:
        if self._closing:
            return
        existing = self._scheduled.get(job_id)
        if existing is not None and not existing.done():
            if force:
                existing.cancel()
            else:
                return
        task = asyncio.create_task(
            self._refresh_later(job_id, force=force),
            name=f"job-status:{job_id}",
        )
        self._scheduled[job_id] = task
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _refresh_later(self, job_id: str, *, force: bool) -> None:
        state = self._states.setdefault(job_id, _PresenterState())
        if not force:
            delay = self.settings.question_progress_interval_sec - (
                time.monotonic() - state.last_edit
            )
            if delay > 0:
                await asyncio.sleep(delay)
        await self.refresh(job_id, force=force)

    async def refresh(self, job_id: str, *, force: bool = False) -> None:
        snapshot = self.manager.snapshot(job_id)
        if snapshot is None:
            return
        lock = self._locks.setdefault(job_id, asyncio.Lock())
        async with lock:
            snapshot = self.manager.snapshot(job_id)
            if snapshot is None:
                return
            state = self._states.setdefault(job_id, _PresenterState())
            text, markup = render_job_status(
                snapshot,
                renewal_sec=self.settings.question_renewal_interval_sec,
                stage_label=state.stage_label,
            )
            if text == state.last_text and not force:
                return
            try:
                await self.bot.edit_message_text(
                    chat_id=snapshot.chat_id,
                    message_id=snapshot.status_message_id,
                    text=text,
                    reply_markup=markup,
                )
            except TelegramRetryAfter as exc:
                await asyncio.sleep(max(0.0, float(exc.retry_after)))
                if self._closing:
                    return
                self.enqueue(job_id, force=True)
                return
            except TelegramBadRequest as exc:
                if not self._is_missing_message(exc) or state.replacement_used:
                    return
                state.replacement_used = True
                kwargs = {}
                if snapshot.topic_id is not None:
                    kwargs["message_thread_id"] = snapshot.topic_id
                try:
                    replacement = await self.bot.send_message(
                        chat_id=snapshot.chat_id,
                        text=text,
                        reply_markup=markup,
                        **kwargs,
                    )
                except Exception:  # noqa: BLE001 - leave parked at next consent boundary
                    return
                updated = await self.manager.set_status_message_id(
                    job_id,
                    snapshot.status_message_id,
                    replacement.message_id,
                )
                if not updated:
                    return
            except Exception:  # noqa: BLE001 - Telegram status failure is not AI failure
                return
            state.last_text = text
            state.last_edit = time.monotonic()

    @staticmethod
    def _is_missing_message(exc: TelegramBadRequest) -> bool:
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "message to edit not found",
                "message not found",
                "message_id_invalid",
            )
        )

    async def close(self) -> None:
        self._closing = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._scheduled.clear()
