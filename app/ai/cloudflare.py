"""Cloudflare Workers AI through its OpenAI-compatible endpoint."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities


def make_cloudflare_provider(settings: Settings) -> OpenAICompatProvider:
    base_url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        f"{settings.cloudflare_account_id}/ai/v1"
    )
    return OpenAICompatProvider(
        name="cloudflare",
        base_url=base_url,
        api_key=settings.cloudflare_api_token,
        model=settings.cloudflare_vision_model,
        timeout=settings.request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision",
            supports_vision=True,
            max_images=settings.max_images_per_request,
        ),
    )
