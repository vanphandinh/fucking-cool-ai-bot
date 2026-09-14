"""Telegram callback validation for renewable question jobs."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

_ACTIONS = frozenset({"c", "s"})


def make_callback_data(job_id: str, generation: int, action: str) -> str:
    if action not in _ACTIONS:
        raise ValueError("invalid job callback action")
    value = f"qj:{job_id}:{int(generation)}:{action}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("callback data exceeds Telegram limit")
    return value


def parse_callback_data(value: str | None) -> tuple[str, int, str] | None:
    parts = str(value or "").split(":")
    if len(parts) != 4 or parts[0] != "qj" or parts[3] not in _ACTIONS:
        return None
    job_id = parts[1].strip()
    if not job_id or len(job_id) > 32:
        return None
    try:
        generation = int(parts[2])
    except ValueError:
        return None
    if generation < 0:
        return None
    return job_id, generation, parts[3]


async def handle_job_callback(query, manager, presenter=None) -> str:
    """Ack first, then validate target metadata through the manager."""
    try:
        await query.answer()
    except Exception:  # noqa: BLE001 - mutation must not depend on Telegram ack success
        pass

    parsed = parse_callback_data(getattr(query, "data", None))
    actor = getattr(getattr(query, "from_user", None), "id", None)
    message = getattr(query, "message", None)
    chat_id = getattr(getattr(message, "chat", None), "id", None)
    message_id = getattr(message, "message_id", None)
    if parsed is None or actor is None or chat_id is None or message_id is None:
        return "invalid"

    job_id, generation, action = parsed
    if action == "c":
        result = await manager.renew(job_id, generation, actor, chat_id, message_id)
    else:
        result = await manager.stop(job_id, actor, chat_id, message_id)

    if presenter is not None:
        try:
            presenter.enqueue(job_id, force=True)
        except Exception:  # noqa: BLE001 - status UI cannot change mutation result
            pass
    return result


def build_job_callback_router(settings, manager, presenter=None) -> Router:
    router = Router()

    @router.callback_query(F.data.startswith("qj:"))
    async def on_job_callback(query: CallbackQuery) -> None:
        await handle_job_callback(query, manager, presenter)

    return router
