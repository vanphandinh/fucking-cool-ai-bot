"""Filters aiogram: allowlist group + trigger (mention / reply-to-bot)."""
from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import Message

from ..config import Settings


class AllowedChat(Filter):
    """Chỉ nhận message trong group/supergroup nằm trong ALLOWED_GROUP_IDS.

    Đọc cấu hình LIVE từ settings mỗi lần gọi (cho phép đổi .env + restart mà
    không lo stale; chi phí không đáng kể với lượng message của group nhỏ).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def __call__(self, message: Message) -> bool:
        chat = message.chat
        if chat.type not in ("group", "supergroup"):
            return False
        return chat.id in self._settings.allowed_group_ids_list


class TriggeredMessage(Filter):
    """Tin nhắn được "gọi" bot: có @username, hoặc reply vào tin của bot.

    - Hỗ trợ cả text lẫn caption (khi user tag bot kèm ảnh/video).
    - Username đọc live từ settings (đã được đồng bộ với Telegram khi khởi động).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def __call__(self, message: Message) -> bool:
        text = (message.text or message.caption or "").strip()
        if not text:
            return False
        username = self._settings.bot_username_clean
        if not username:
            return False
        if text.startswith("/"):
            return False  # lệnh / sẽ do handler riêng xử lý
        if f"@{username}" in text.lower():
            return True
        replied = message.reply_to_message
        if replied and replied.from_user and replied.from_user.is_bot:
            replied_username = (replied.from_user.username or "").lower()
            if replied_username == username:
                return True
        return False
