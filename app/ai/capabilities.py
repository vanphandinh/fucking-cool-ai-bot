"""Capability metadata attached to provider slots."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderCapabilities:
    route: str = "text"  # text | vision
    supports_vision: bool = False
    max_images: int = 0

    def accepts(self, *, requires_vision: bool, image_count: int) -> bool:
        if requires_vision:
            return self.route == "vision" and self.supports_vision and self.max_images >= image_count
        return self.route == "text"
