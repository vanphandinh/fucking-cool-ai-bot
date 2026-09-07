"""Provider Groq — text and configurable vision slots."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities

_BASE_URL = "https://api.groq.com/openai/v1"


def make_groq_provider(
    settings: Settings,
    *,
    name: str = "groq",
    model: str | None = None,
    vision: bool = False,
    max_images: int | None = None,
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name=name,
        base_url=_BASE_URL,
        api_key=settings.groq_api_key,
        model=model or settings.groq_model,
        timeout=settings.request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=(max_images or settings.max_images_per_request) if vision else 0,
        ),
    )
