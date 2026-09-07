"""Telegram image ingestion into ephemeral in-memory attachments."""

from __future__ import annotations

import io

from aiogram.types import Message

from ..config import Settings
from ..core.request import ImageAttachment

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


class MediaValidationError(Exception):
    pass


class UnsupportedImageFormat(MediaValidationError):
    pass


class ImageTooLarge(MediaValidationError):
    pass


class TelegramMediaLoader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def load(self, message: Message) -> tuple[ImageAttachment, ...]:
        images: list[ImageAttachment] = []
        current = await self._from_message(message, "current")
        if current is not None:
            images.append(current)
        reply = getattr(message, "reply_to_message", None)
        if reply is not None:
            replied = await self._from_message(reply, "reply")
            if replied is not None:
                images.append(replied)

        if len(images) > self.settings.max_images_per_request:
            raise MediaValidationError("Quá nhiều ảnh trong một yêu cầu")
        total = sum(len(image.data) for image in images)
        if total > self.settings.max_total_image_bytes:
            raise ImageTooLarge("Tổng dung lượng ảnh quá lớn")
        return tuple(images)

    async def _from_message(self, message: Message, source: str) -> ImageAttachment | None:
        file_id: str | None = None
        mime_type: str | None = None
        declared_size: int | None = None
        photos = getattr(message, "photo", None)
        document = getattr(message, "document", None)

        if photos:
            photo = photos[-1]
            file_id = photo.file_id
            mime_type = "image/jpeg"
            declared_size = photo.file_size
        elif document:
            mime_type = document.mime_type
            if mime_type is None:
                return None
            if mime_type not in _ALLOWED_MIME:
                if mime_type.startswith("image/"):
                    raise UnsupportedImageFormat(mime_type)
                return None
            file_id = document.file_id
            declared_size = document.file_size
        else:
            return None

        if declared_size is not None and declared_size > self.settings.max_image_bytes:
            raise ImageTooLarge("Ảnh quá lớn")

        buf = io.BytesIO()
        try:
            await message.bot.download(file_id, destination=buf)
        except Exception as exc:  # noqa: BLE001
            raise MediaValidationError("Không tải được ảnh từ Telegram") from exc
        data = buf.getvalue()
        if len(data) > self.settings.max_image_bytes:
            raise ImageTooLarge("Ảnh quá lớn")
        return ImageAttachment(mime_type=mime_type, data=data, source=source)  # type: ignore[arg-type]
