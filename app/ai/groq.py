"""Provider Groq (model mở, rất nhanh) — fallback chính."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider

_BASE_URL = "https://api.groq.com/openai/v1"


def make_groq_provider(settings: Settings) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name="groq",
        base_url=_BASE_URL,
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        timeout=settings.request_timeout_sec,
    )
