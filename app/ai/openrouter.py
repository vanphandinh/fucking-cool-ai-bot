"""Provider OpenRouter model :free — fallback cuối."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider

_BASE_URL = "https://openrouter.ai/api/v1"


def make_openrouter_provider(settings: Settings) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name="openrouter",
        base_url=_BASE_URL,
        api_key=settings.openrouter_api_key,
        model=settings.openrouter_model,
        timeout=settings.request_timeout_sec,
        extra_headers={"X-Title": "FuckingCoolAI bot"},
    )
