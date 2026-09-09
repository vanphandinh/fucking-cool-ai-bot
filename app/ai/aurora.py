"""Aurora ChatGPT Web gateway provider integration."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities

_AUTH_FAILURE_COOLDOWN_SEC = 60.0


def make_aurora_provider(settings: Settings, *, name: str = "aurora") -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name=name,
        base_url=settings.aurora_base_url,
        api_key=settings.aurora_api_key,
        model=settings.aurora_model,
        timeout=settings.aurora_request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="text",
            supports_vision=False,
            max_images=0,
        ),
        auth_failure_cooldown_sec=_AUTH_FAILURE_COOLDOWN_SEC,
    )
