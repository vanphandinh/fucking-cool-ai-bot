"""B.AI provider integration for the currently promoted zero-credit models."""

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
