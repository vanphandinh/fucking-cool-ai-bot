"""Provider Google Gemini qua endpoint OpenAI-compatible (miễn phí)."""
from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def make_gemini_provider(settings: Settings) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name="gemini",
        base_url=_BASE_URL,
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout=settings.request_timeout_sec,
    )
