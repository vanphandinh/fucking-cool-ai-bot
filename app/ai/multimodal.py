"""Build protocol-independent multimodal user message parts."""

from __future__ import annotations

from .contracts import ImagePart, MessagePart, TextPart
from ..core.request import UserRequest


def build_user_parts(request: UserRequest) -> tuple[MessagePart, ...]:
    if not request.requires_vision:
        text_parts: list[str] = []
        if request.quoted_text:
            text_parts.append(
                f"Nội dung tin đang được reply:\n{request.quoted_text[:1500]}"
            )
        text_parts.append(f"Câu hỏi của người dùng:\n{request.text[:4000]}")
        return (TextPart("\n\n".join(text_parts)),)

    parts: list[MessagePart] = []
    if request.quoted_text:
        parts.append(
            TextPart(
                f"Nội dung tin đang được reply:\n{request.quoted_text[:1500]}"
            )
        )
    parts.extend(
        ImagePart(image.mime_type, image.data)
        for image in request.images
        if image.source == "reply"
    )
    parts.append(TextPart(f"Câu hỏi của người dùng:\n{request.text[:4000]}"))
    parts.extend(
        ImagePart(image.mime_type, image.data)
        for image in request.images
        if image.source == "current"
    )
    return tuple(parts)
