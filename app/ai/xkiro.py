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
    api_key: str | None = None,
    model: str | None = None,
    credential_id: str = "cred-1",
    target_id: str | None = None,
) -> OpenAICompatProvider:
    configured_key = str(
        api_key if api_key is not None else settings.xkiro_api_key or ""
    ).strip()
    if not configured_key:
        raise ValueError("XKIRO_API_KEY là bắt buộc khi khởi tạo xKiro provider")

    configured_model = str(
        model
        if model is not None
        else (settings.xkiro_vision_model if vision else settings.xkiro_text_model)
    ).strip()
    setting_name = "XKIRO_VISION_MODEL" if vision else "XKIRO_TEXT_MODEL"
    if not configured_model:
        raise ValueError(f"{setting_name} là bắt buộc khi khởi tạo xKiro provider")

    provider = OpenAICompatProvider(
        name=name,
        base_url=_BASE_URL,
        api_key=configured_key,
        model=configured_model,
        timeout=settings.xkiro_request_timeout_sec,
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


def build_xkiro_provider_slots(settings: Settings) -> list[AIProvider]:
    keys = settings.xkiro_api_keys_list
    if not keys:
        return []

    slots: list[AIProvider] = []
    if "xkiro" in settings.text_provider_order_list:
        for model_index, model in enumerate(settings.xkiro_text_models_list, start=1):
            for key_index, api_key in enumerate(keys, start=1):
                slots.append(
                    make_xkiro_provider(
                        settings,
                        api_key=api_key,
                        model=model,
                        credential_id=f"cred-{key_index}",
                        target_id=f"xkiro:text:m{model_index}:c{key_index}",
                    )
                )
    if settings.vision_enabled and "xkiro" in settings.vision_provider_order_list:
        for model_index, model in enumerate(settings.xkiro_vision_models_list, start=1):
            for key_index, api_key in enumerate(keys, start=1):
                slots.append(
                    make_xkiro_provider(
                        settings,
                        vision=True,
                        api_key=api_key,
                        model=model,
                        credential_id=f"cred-{key_index}",
                        target_id=f"xkiro:vision:m{model_index}:c{key_index}",
                    )
                )
    return slots
