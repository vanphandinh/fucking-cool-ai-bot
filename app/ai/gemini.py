"""Provider Google Gemini qua endpoint OpenAI-compatible."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def make_gemini_provider(
    settings: Settings,
    *,
    name: str = "gemini",
    model: str | None = None,
    vision: bool = False,
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name=name,
        base_url=_BASE_URL,
        api_key=settings.gemini_api_key,
        model=model or settings.gemini_model,
        timeout=settings.request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=settings.max_images_per_request if vision else 0,
        ),
    )
