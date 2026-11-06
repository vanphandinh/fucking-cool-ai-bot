"""Generic runtime configuration for declarative AI providers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from app.ai.catalog import ProviderProfile
    from app.config import Settings


MAX_AI_PROVIDER_TARGETS = 64
_PROVIDER_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


def parse_unique_csv_preserve_case(raw: str | None) -> list[str]:
    """Parse comma-separated credentials/models preserving case and first occurrence."""

    out: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").split(","):
        value = part.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


class ProviderRuntimeSettings(BaseModel):
    """Environment-owned values for one provider family."""

    model_config = ConfigDict(hide_input_in_errors=True)

    api_keys: str = Field(default="", repr=False)
    base_url: str = ""
    text_models: str = ""
    vision_models: str = ""
    provider: str = ""
    request_timeout_sec: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )

    @field_validator("provider")
    @classmethod
    def _check_provider_slugs(cls, value: str) -> str:
        for part in (value or "").split(","):
            token = part.strip()
            if token and _PROVIDER_SLUG_RE.fullmatch(token) is None:
                raise ValueError(
                    "provider must be a comma-separated list of provider slugs"
                )
        return value

    @property
    def api_keys_list(self) -> list[str]:
        return parse_unique_csv_preserve_case(self.api_keys)

    @property
    def text_models_list(self) -> list[str]:
        return parse_unique_csv_preserve_case(self.text_models)

    @property
    def vision_models_list(self) -> list[str]:
        return parse_unique_csv_preserve_case(self.vision_models)

    @property
    def provider_slugs_list(self) -> list[str]:
        return parse_unique_csv_preserve_case(self.provider)

    def resolve_base_url(self, profile: ProviderProfile) -> str:
        return self.base_url.strip() or profile.default_base_url

    def resolve_timeout(self, profile: ProviderProfile) -> float:
        return (
            self.request_timeout_sec
            if self.request_timeout_sec is not None
            else profile.default_timeout_sec
        )


def provider_runtime(settings: Settings, provider_id: str) -> ProviderRuntimeSettings:
    """Return runtime settings for one provider id, defaulting to an empty pool."""

    normalized = provider_id.strip().lower()
    runtime = settings.ai_providers.get(normalized)
    if runtime is None:
        return ProviderRuntimeSettings()
    if isinstance(runtime, ProviderRuntimeSettings):
        return runtime
    return ProviderRuntimeSettings.model_validate(runtime)
