"""Telegram delivery for Internet image-search results."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def send_image_results(
    bot,
    chat_id: int,
    images: list[dict],
    message_thread_id: int | None = None,
) -> int:
    """Send images by URL without downloading them through the bot server."""
    sent = 0
    kwargs = {"chat_id": chat_id}
    if message_thread_id:
        kwargs["message_thread_id"] = message_thread_id

    for item in images:
        if not isinstance(item, dict):
            continue
        full = str(item.get("image_url") or "").strip()
        thumb = str(item.get("thumbnail_url") or "").strip()
        candidates = []
        for url in (full, thumb):
            if url.startswith(("http://", "https://")) and url not in candidates:
                candidates.append(url)
        for photo in candidates:
            try:
                await bot.send_photo(photo=photo, **kwargs)
                sent += 1
                break
            except Exception as exc:  # noqa: BLE001
                logger.info("Telegram không gửi được ảnh URL, thử fallback: %s", exc)
    return sent
