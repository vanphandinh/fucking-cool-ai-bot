"""Telegram image/video ingestion into ephemeral in-memory attachments.

Bot chỉ xử lý media khi đã được mention/tag (filter quyết định ở tầng
router); module này không tự trigger bất kỳ xử lý nào.

Video (kể cả từ tin nhắn forward) được chuyển thành 1 frame JPEG giữa video
qua ffmpeg, fallback về thumbnail có sẵn của Telegram khi thiếu ffmpeg/lỗi.
Forward thường giữ nguyên media trên Message nên không cần logic
forward_origin riêng. Story forward không có file_id nên Bot API không lấy được.
"""

from __future__ import annotations

import io

from aiogram.types import Message

from ..config import Settings
from ..core.request import ImageAttachment
from .video_frames import VideoFrameError, extract_middle_frame

_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}


class MediaValidationError(Exception):
    pass


class UnsupportedImageFormat(MediaValidationError):
    pass


class ImageTooLarge(MediaValidationError):
    pass


class _BoundedBytesIO(io.BytesIO):
    """BytesIO destination that rejects a Telegram download before it exceeds the limit."""

    def __init__(self, max_bytes: int) -> None:
        super().__init__()
        self._max_bytes = max_bytes

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self._max_bytes:
            raise ImageTooLarge("Tệp quá lớn")
        return super().write(data)


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
        # Reply liên chat/forum-topic (hay gặp khi reply vào tin forward):
        # ExternalReplyInfo chỉ có media, không có text/caption.
        external = getattr(message, "external_reply", None)
        if external is not None:
            # Map về "reply" để giữ nguyên ImageSource literal và thứ tự
            # multimodal (reply trước, current sau).
            ext = await self._from_message(external, "reply")
            if ext is not None:
                images.append(ext)

        if len(images) > self.settings.max_images_per_request:
            raise MediaValidationError("Quá nhiều ảnh trong một yêu cầu")
        total = sum(len(image.data) for image in images)
        if total > self.settings.max_total_image_bytes:
            raise ImageTooLarge("Tổng dung lượng ảnh quá lớn")
        return tuple(images)

    async def _download(self, message: Message, file_id: str, max_bytes: int) -> bytes:
        buf = _BoundedBytesIO(max_bytes)
        try:
            await message.bot.download(file_id, destination=buf)
        except ImageTooLarge:
            raise
        except Exception as exc:  # noqa: BLE001
            raise MediaValidationError("Không tải được tệp từ Telegram") from exc
        data = buf.getvalue()
        if len(data) > max_bytes:
            raise ImageTooLarge("Tệp quá lớn")
        return data

    async def _video_thumbnail_fallback(
        self, message: Message, thumb_file_id: str | None, source: str
    ) -> ImageAttachment | None:
        if not thumb_file_id:
            return None
        data = await self._download(message, thumb_file_id, self.settings.max_image_bytes)
        return ImageAttachment(mime_type="image/jpeg", data=data, source=source)  # type: ignore[arg-type]

    async def _from_video_bytes(
        self,
        message: Message,
        video_bytes: bytes,
        duration_sec: float | None,
        thumb_file_id: str | None,
        source: str,
    ) -> ImageAttachment | None:
        try:
            frame = await extract_middle_frame(
                video_bytes,
                duration_sec=duration_sec,
                timeout_sec=self.settings.video_ffmpeg_timeout_sec,
            )
        except VideoFrameError:
            return await self._video_thumbnail_fallback(message, thumb_file_id, source)
        if len(frame) > self.settings.max_image_bytes:
            # Frame trích ra vẫn phải nằm trong hạn ảnh của vision pipeline.
            return await self._video_thumbnail_fallback(message, thumb_file_id, source)
        return ImageAttachment(mime_type="image/jpeg", data=frame, source=source)  # type: ignore[arg-type]

    def _video_descriptor(
        self, message: Message
    ) -> tuple[str, int | None, float | None, str | None] | None:
        """Trả về (file_id, file_size, duration, thumb_file_id) nếu message có video.

        Hỗ trợ video/animation/video_note (tin tròn) và document dạng video.
        """
        for attr in ("video", "animation", "video_note"):
            media = getattr(message, attr, None)
            if media is None:
                continue
            thumb_id: str | None = None
            thumb = getattr(media, "thumbnail", None)
            if thumb is not None:
                thumb_id = thumb.file_id
            if thumb_id is None:
                # Video còn có thể mang danh sách cover (PhotoSize).
                cover = getattr(media, "cover", None)
                if cover:
                    thumb_id = cover[-1].file_id
            duration = getattr(media, "duration", None)
            return (
                media.file_id,
                getattr(media, "file_size", None),
                float(duration) if duration is not None else None,
                thumb_id,
            )
        document = getattr(message, "document", None)
        if document is not None:
            mime = getattr(document, "mime_type", None) or ""
            if mime.startswith("video/"):
                return (document.file_id, document.file_size, None, None)
        return None

    async def _from_message(self, message: Message, source: str) -> ImageAttachment | None:
        photos = getattr(message, "photo", None)
        document = getattr(message, "document", None)

        if photos:
            photo = photos[-1]
            if photo.file_size is not None and photo.file_size > self.settings.max_image_bytes:
                raise ImageTooLarge("Ảnh quá lớn")
            data = await self._download(message, photo.file_id, self.settings.max_image_bytes)
            return ImageAttachment(mime_type="image/jpeg", data=data, source=source)  # type: ignore[arg-type]

        if document is not None:
            mime = getattr(document, "mime_type", None) or ""
            if mime in _ALLOWED_MIME:
                max_bytes = self.settings.max_image_bytes
                if document.file_size is not None and document.file_size > max_bytes:
                    raise ImageTooLarge("Ảnh quá lớn")
                data = await self._download(message, document.file_id, max_bytes)
                return ImageAttachment(mime_type=mime, data=data, source=source)  # type: ignore[arg-type]
            if mime.startswith("video/"):
                return await self._from_video(
                    message,
                    (document.file_id, document.file_size, None, None),
                    source,
                )
            if not mime:
                return None
            if mime.startswith("image/"):
                raise UnsupportedImageFormat(mime)
            return None

        video = self._video_descriptor(message)
        if video is None:
            return None
        return await self._from_video(message, video, source)

    async def _from_video(
        self,
        message: Message,
        video: tuple[str, int | None, float | None, str | None],
        source: str,
    ) -> ImageAttachment | None:
        file_id, declared_size, duration, thumb_id = video
        if not self.settings.vision_video_enabled:
            return None
        if duration is not None and duration > self.settings.max_video_duration_sec:
            raise ImageTooLarge("Video quá dài để phân tích")
        if declared_size is not None and declared_size > self.settings.max_video_bytes:
            raise ImageTooLarge("Video quá lớn để phân tích")
        video_bytes = await self._download(message, file_id, self.settings.max_video_bytes)
        return await self._from_video_bytes(message, video_bytes, duration, thumb_id, source)
