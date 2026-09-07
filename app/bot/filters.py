"""Filters aiogram: allowlist group + trigger (mention / reply-to-bot)."""
from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import Message

from ..config import Settings


class AllowedChat(Filter):
    """Chỉ nhận message trong group/supergroup nằm trong ALLOWED_GROUP_IDS."""

    def __init__(self, settings: Settings) -> None:
        self._allowed_ids = set(settings.allowed_group_ids_list)

    async def __call__(self, message: Message) -> bool:
        chat = message.chat
        if chat.type not in ("group", "supergroup"):
            return False
        return chat.id in self._allowed_ids


class TriggeredMessage(Filter):
    """Tin nhắn được "gọi" bot: có @username, hoặc reply vào tin của bot."""

    def __init__(self, settings: Settings) -> None:
        self._username = settings.bot_username_clean

    async def __call__(self, message: Message) -> bool:
        text = message.text
        if not text:
            return False
        if text.startswith("/"):
            return False  # lệnh / sẽ do handler riêng xử lý
        if f"@{self._username}" in text.lower():
            return True
        replied = message.reply_to_message
        if replied and replied.from_user and replied.from_user.is_bot:
            replied_username = (replied.from_user.username or "").lower()
            if replied_username == self._username and text.strip():
                return True
        return False
