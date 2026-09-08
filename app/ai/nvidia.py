"""NVIDIA NIM provider adapter for hosted/commercial/self-hosted OpenAI-compatible endpoints."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities


def make_nvidia_provider(
    settings: Settings,
    *,
    name: str = "nvidia",
    model: str | None = None,
    vision: bool = False,
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name=name,
        base_url=settings.nvidia_nim_base_url,
        api_key=settings.nvidia_nim_api_key,
        model=model or (
            settings.nvidia_nim_vision_model if vision else settings.nvidia_nim_text_model
        ),
        timeout=settings.request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=settings.max_images_per_request if vision else 0,
        ),
        request_defaults={"stream": False},
    )
