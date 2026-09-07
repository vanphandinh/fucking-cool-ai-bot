"""Build OpenAI-compatible multimodal user content at the provider boundary."""

from __future__ import annotations

import base64

from ..core.request import ImageAttachment, UserRequest


def _image_part(image: ImageAttachment) -> dict:
    encoded = base64.b64encode(image.data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{image.mime_type};base64,{encoded}"},
    }


def build_user_content(request: UserRequest) -> str | list[dict]:
    if not request.requires_vision:
        parts: list[str] = []
        if request.quoted_text:
            parts.append(f"Nội dung tin đang được reply:\n{request.quoted_text[:1500]}")
        parts.append(f"Câu hỏi của người dùng:\n{request.text[:4000]}")
        return "\n\n".join(parts)

    content: list[dict] = []
    if request.quoted_text:
        content.append(
            {"type": "text", "text": f"Nội dung tin đang được reply:\n{request.quoted_text[:1500]}"}
        )
    content.extend(_image_part(image) for image in request.images if image.source == "reply")
    content.append({"type": "text", "text": f"Câu hỏi của người dùng:\n{request.text[:4000]}"})
    content.extend(_image_part(image) for image in request.images if image.source == "current")
    return content
