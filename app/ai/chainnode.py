"""Chainnode OpenAI-compatible provider integration."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities
from .provider import AIProvider


def make_chainnode_provider(
    settings: Settings,
    *,
    name: str = "chainnode",
    vision: bool = False,
) -> OpenAICompatProvider:
    model = str(
        settings.chainnode_vision_model if vision else settings.chainnode_text_model
    ).strip()
    setting_name = "CHAINNODE_VISION_MODEL" if vision else "CHAINNODE_TEXT_MODEL"
    if not model:
        raise ValueError(f"{setting_name} là bắt buộc khi khởi tạo Chainnode provider")

    return OpenAICompatProvider(
        name=name,
        base_url=settings.chainnode_base_url,
        api_key=settings.chainnode_api_key,
        model=model,
        timeout=settings.chainnode_request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=1 if vision else 0,
        ),
        explicit_stream=False,
    )


def build_chainnode_provider_slots(settings: Settings) -> list[AIProvider]:
    if not settings.chainnode_api_key:
        return []

    slots: list[AIProvider] = []
    if "chainnode" in settings.text_provider_order_list and settings.chainnode_text_model:
        slots.append(make_chainnode_provider(settings))
    if (
        settings.vision_enabled
        and "chainnode" in settings.vision_provider_order_list
        and settings.chainnode_vision_model
    ):
        slots.append(make_chainnode_provider(settings, vision=True))
    return slots
