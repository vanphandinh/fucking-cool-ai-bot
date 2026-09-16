"""xKiro OpenAI-compatible provider integration."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities
from .provider import AIProvider

_BASE_URL = "https://api.xkiro.com/v1"


def make_xkiro_provider(
    settings: Settings,
    *,
    name: str = "xkiro",
    vision: bool = False,
) -> OpenAICompatProvider:
    api_key = str(settings.xkiro_api_key or "").strip()
    if not api_key:
        raise ValueError("XKIRO_API_KEY là bắt buộc khi khởi tạo xKiro provider")

    model = str(
        settings.xkiro_vision_model if vision else settings.xkiro_text_model
    ).strip()
    setting_name = "XKIRO_VISION_MODEL" if vision else "XKIRO_TEXT_MODEL"
    if not model:
        raise ValueError(f"{setting_name} là bắt buộc khi khởi tạo xKiro provider")

    return OpenAICompatProvider(
        name=name,
        base_url=_BASE_URL,
        api_key=api_key,
        model=model,
        timeout=settings.xkiro_request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=1 if vision else 0,
        ),
        explicit_stream=False,
    )


def build_xkiro_provider_slots(settings: Settings) -> list[AIProvider]:
    if not str(settings.xkiro_api_key or "").strip():
        return []

    slots: list[AIProvider] = []
    if "xkiro" in settings.text_provider_order_list and settings.xkiro_text_model:
        slots.append(make_xkiro_provider(settings))
    if (
        settings.vision_enabled
        and "xkiro" in settings.vision_provider_order_list
        and settings.xkiro_vision_model
    ):
        slots.append(make_xkiro_provider(settings, vision=True))
    return slots
