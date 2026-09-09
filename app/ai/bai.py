"""B.AI provider integration for the currently promoted zero-credit models."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities
from .provider import AIProvider

_BASE_URL = "https://api.b.ai/v1"
SUPPORTED_PROMO_MODELS = frozenset(
    {
        "qwen3.8-flash",
        "mimo-v2.5",
        "hy3",
        "glm-5.3-flash",
    }
)
_VISION_MODELS = frozenset({"qwen3.8-flash", "mimo-v2.5", "glm-5.3-flash"})


def model_supports_vision(model: str) -> bool:
    return model in _VISION_MODELS


def _validate_model(model: str, *, vision: bool) -> None:
    if model not in SUPPORTED_PROMO_MODELS:
        raise ValueError(f"B.AI model không thuộc promotion được hỗ trợ: {model!r}")
    if vision and not model_supports_vision(model):
        raise ValueError(f"B.AI model không hỗ trợ vision: {model!r}")


def make_bai_provider(
    settings: Settings,
    *,
    name: str = "bai",
    model: str | None = None,
    vision: bool = False,
) -> OpenAICompatProvider:
    selected_model = model or (settings.bai_vision_model if vision else settings.bai_text_model)
    _validate_model(selected_model, vision=vision)
    provider = OpenAICompatProvider(
        name=name,
        base_url=_BASE_URL,
        api_key=settings.bai_api_key,
        model=selected_model,
        timeout=settings.bai_request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=1 if vision else 0,
        ),
    )
    # B.AI may continue emitting function calls from prior tool history even when
    # the tools array is omitted. The plain recovery pass must explicitly force
    # tool selection off so it can synthesize a final answer from tool results.
    provider.force_tool_choice_none_when_no_tools = True
    return provider


def build_bai_provider_slots(settings: Settings) -> list[AIProvider]:
    """Build only B.AI route slots that are selected by provider order."""
    if not settings.bai_api_key:
        return []

    text_enabled = "bai" in settings.text_provider_order_list
    vision_enabled = "bai" in settings.vision_provider_order_list

    slots: list[AIProvider] = []
    if text_enabled and settings.bai_text_model:
        slots.append(make_bai_provider(settings, name="bai"))
    if vision_enabled and settings.vision_enabled and settings.bai_vision_model:
        slots.append(make_bai_provider(settings, name="bai", vision=True))
    return slots
