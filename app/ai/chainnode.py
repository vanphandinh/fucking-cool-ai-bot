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
    api_key: str | None = None,
    model: str | None = None,
    credential_id: str = "cred-1",
    target_id: str | None = None,
) -> OpenAICompatProvider:
    configured_key = str(
        api_key if api_key is not None else settings.chainnode_api_key or ""
    ).strip()
    if not configured_key:
        raise ValueError("CHAINNODE_API_KEY là bắt buộc khi khởi tạo Chainnode provider")

    configured_model = str(
        model
        if model is not None
        else (
            settings.chainnode_vision_model
            if vision
            else settings.chainnode_text_model
        )
    ).strip()
    setting_name = "CHAINNODE_VISION_MODEL" if vision else "CHAINNODE_TEXT_MODEL"
    if not configured_model:
        raise ValueError(f"{setting_name} là bắt buộc khi khởi tạo Chainnode provider")

    provider = OpenAICompatProvider(
        name=name,
        base_url=settings.chainnode_base_url,
        api_key=configured_key,
        model=configured_model,
        timeout=settings.chainnode_request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=1 if vision else 0,
        ),
        explicit_stream=False,
    )
    route = "vision" if vision else "text"
    provider.credential_id = credential_id
    provider.target_id = target_id or f"{name}:{route}:{configured_model}:{credential_id}"
    return provider


def build_chainnode_provider_slots(settings: Settings) -> list[AIProvider]:
    keys = settings.chainnode_api_keys_list
    if not keys:
        return []

    slots: list[AIProvider] = []
    if "chainnode" in settings.text_provider_order_list:
        for model_index, model in enumerate(settings.chainnode_text_models_list, start=1):
            for key_index, api_key in enumerate(keys, start=1):
                slots.append(
                    make_chainnode_provider(
                        settings,
                        api_key=api_key,
                        model=model,
                        credential_id=f"cred-{key_index}",
                        target_id=f"chainnode:text:m{model_index}:c{key_index}",
                    )
                )
    if settings.vision_enabled and "chainnode" in settings.vision_provider_order_list:
        for model_index, model in enumerate(
            settings.chainnode_vision_models_list,
            start=1,
        ):
            for key_index, api_key in enumerate(keys, start=1):
                slots.append(
                    make_chainnode_provider(
                        settings,
                        vision=True,
                        api_key=api_key,
                        model=model,
                        credential_id=f"cred-{key_index}",
                        target_id=f"chainnode:vision:m{model_index}:c{key_index}",
                    )
                )
    return slots
