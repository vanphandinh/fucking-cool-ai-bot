"""NVIDIA NIM provider adapter for hosted/commercial/self-hosted OpenAI-compatible endpoints."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities

_NEMOTRON_LIGHTNING_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


def make_nvidia_provider(
    settings: Settings,
    *,
    name: str = "nvidia",
    model: str | None = None,
    vision: bool = False,
) -> OpenAICompatProvider:
    resolved_model = model or (
        settings.nvidia_nim_vision_model if vision else settings.nvidia_nim_text_model
    )
    request_defaults: dict = {"stream": False}
    if not vision and resolved_model == _NEMOTRON_LIGHTNING_MODEL:
        request_defaults.update(
            {
                "max_tokens": settings.nvidia_nim_max_tokens,
                "chat_template_kwargs": {
                    "enable_thinking": settings.nvidia_nim_enable_thinking,
                },
            }
        )
        if settings.nvidia_nim_enable_thinking:
            request_defaults["thinking_token_budget"] = (
                settings.nvidia_nim_thinking_token_budget
            )

    return OpenAICompatProvider(
        name=name,
        base_url=settings.nvidia_nim_base_url,
        api_key=settings.nvidia_nim_api_key,
        model=resolved_model,
        timeout=settings.request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=settings.max_images_per_request if vision else 0,
        ),
        request_defaults=request_defaults,
    )
