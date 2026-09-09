"""Aurora ChatGPT Web gateway provider family."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities
from .provider import AIProvider

_AUTH_FAILURE_COOLDOWN_SEC = 60.0


def make_aurora_provider(
    settings: Settings,
    *,
    name: str = "aurora",
) -> OpenAICompatProvider:
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


def build_aurora_provider_slots(settings: Settings) -> list[AIProvider]:
    """Build Aurora only when the text family is selected and configured."""
    if "aurora" not in settings.text_provider_order_list:
        return []
    if not (
        settings.aurora_base_url.strip()
        and settings.aurora_api_key
        and settings.aurora_model
    ):
        return []
    return [make_aurora_provider(settings)]
