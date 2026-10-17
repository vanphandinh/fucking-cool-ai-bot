"""Canonical provider-independent user request, including ephemeral image bytes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ImageSource = Literal["current", "reply"]


@dataclass(frozen=True)
class ImageAttachment:
    mime_type: str
    data: bytes
    source: ImageSource = "current"

    def __post_init__(self) -> None:
        if self.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise ValueError(f"Unsupported image MIME: {self.mime_type}")
        if not isinstance(self.data, bytes):
            raise TypeError("ImageAttachment.data must be bytes")


@dataclass(frozen=True)
class UserRequest:
    text: str
    quoted_text: str | None = None
    images: tuple[ImageAttachment, ...] = ()

    @property
    def requires_vision(self) -> bool:
        return bool(self.images)
