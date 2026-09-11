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
) -> OpenAICompatProvider:
    model = str(settings.chainnode_text_model or "").strip()
    if not model:
        raise ValueError(
            "CHAINNODE_TEXT_MODEL là bắt buộc khi khởi tạo Chainnode provider"
        )
    return OpenAICompatProvider(
        name=name,
        base_url=settings.chainnode_base_url,
        api_key=settings.chainnode_api_key,
        model=model,
        timeout=settings.chainnode_request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="text",
            supports_vision=False,
            max_images=0,
        ),
        explicit_stream=False,
    )


def build_chainnode_provider_slots(settings: Settings) -> list[AIProvider]:
    if not settings.chainnode_api_key:
        return []
    if "chainnode" not in settings.text_provider_order_list:
        return []
    return [make_chainnode_provider(settings)]
