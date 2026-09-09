"""Cấu hình ứng dụng — đọc biến môi trường từ .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SEARCH_BACKENDS = ("auto", "searxng", "ddgs")
_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def parse_csv_ints(raw: str | None) -> list[int]:
    out: list[int] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out


def parse_csv(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def parse_unique_csv(raw: str | None) -> list[str]:
    """Parse a CSV order while preserving the first occurrence of each slot."""
    out: list[str] = []
    seen: set[str] = set()
    for part in parse_csv(raw):
        if part in seen:
            continue
        seen.add(part)
        out.append(part)
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    bot_username: str = "FuckingCoolAIbot"
    allowed_group_ids: str = ""
    admin_ids: str = ""
    learn_group_id_mode: bool = False

    # Text pool — free-tier-first defaults.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    openrouter_api_key: str = ""
    openrouter_model: str = "openrouter/free"
    bai_api_key: str = ""
    bai_text_model: str = "qwen3.8-flash"
    bai_request_timeout_sec: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_text_model: str = "@cf/zai-org/glm-4.7-flash"
    text_provider_order: str = "bai,gemini,groq,cloudflare,openrouter"

    # Vision pool.
    vision_enabled: bool = True
    gemini_vision_model: str = "gemini-3.8-flash"
    groq_vision_models: str = "qwen/qwen3.8-27b,qwen/qwen3.6-27b"
    cloudflare_vision_model: str = "@cf/google/gemma-4-26b-a4b-it"
    bai_vision_model: str = "qwen3.8-flash"
    vision_provider_order: str = "bai,gemini,groq_qwen38,cloudflare,groq_qwen36"
    max_images_per_request: int = Field(default=3, ge=1)
    max_image_bytes: int = Field(default=8388608, ge=1)
    max_total_image_bytes: int = Field(default=12582912, ge=1)

    # Unified free/self-hosted-first search policy for both text and image search.
    search_backend: str = "auto"
    searxng_url: str = ""
    image_search_max_results: int = Field(default=4, ge=1, le=8)
    x_fetch_enabled: bool = True

    max_questions_per_min_per_user: int = Field(default=3, ge=0)
    max_context_turns: int = Field(default=6, ge=1)
    max_tool_rounds: int = Field(default=2, ge=0)
    request_timeout_sec: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    question_timeout_sec: float = Field(default=180.0, gt=0, allow_inf_nan=False)
    log_level: str = "INFO"

    @field_validator("search_backend")
    @classmethod
    def _check_search_backend(cls, value: str) -> str:
        backend = (value or "").strip().lower()
        if backend not in _SEARCH_BACKENDS:
            allowed = " | ".join(_SEARCH_BACKENDS)
            raise ValueError(f"SEARCH_BACKEND không hợp lệ: {value!r} (cho phép: {allowed})")
        return backend

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = (value or "INFO").strip().upper()
        if level == "WARN":
            level = "WARNING"
        if level not in _LOG_LEVELS:
            allowed = " | ".join(_LOG_LEVELS)
            raise ValueError(f"LOG_LEVEL không hợp lệ: {value!r} (cho phép: {allowed})")
        return level

    @property
    def allowed_group_ids_list(self) -> list[int]:
        return parse_csv_ints(self.allowed_group_ids)

    @property
    def admin_ids_list(self) -> list[int]:
        return parse_csv_ints(self.admin_ids)

    @property
    def bot_username_clean(self) -> str:
        return self.bot_username.lower().lstrip("@")

    @property
    def text_provider_order_list(self) -> list[str]:
        return parse_unique_csv(self.text_provider_order)

    @property
    def groq_vision_models_list(self) -> list[str]:
        return parse_csv(self.groq_vision_models)

    @property
    def vision_provider_order_list(self) -> list[str]:
        return parse_unique_csv(self.vision_provider_order)

    @property
    def configured_provider_names(self) -> list[str]:
        available: set[str] = set()
        if self.gemini_api_key and self.gemini_model:
            available.add("gemini")
        if self.groq_api_key and self.groq_model:
            available.add("groq")
        if self.openrouter_api_key and self.openrouter_model:
            available.add("openrouter")
        if self.bai_api_key and self.bai_text_model:
            available.add("bai")
        if (
            self.cloudflare_account_id
            and self.cloudflare_api_token
            and self.cloudflare_text_model
        ):
            available.add("cloudflare")
        return [name for name in self.text_provider_order_list if name in available]

    @property
    def configured_vision_provider_names(self) -> list[str]:
        if not self.vision_enabled:
            return []
        available: set[str] = set()
        if self.gemini_api_key and self.gemini_vision_model:
            available.add("gemini")
        if self.groq_api_key:
            models = self.groq_vision_models_list
            if len(models) >= 1:
                available.add("groq_qwen38")
            if len(models) >= 2:
                available.add("groq_qwen36")
        if self.bai_api_key and self.bai_vision_model:
            available.add("bai")
        if (
            self.cloudflare_account_id
            and self.cloudflare_api_token
            and self.cloudflare_vision_model
        ):
            available.add("cloudflare")
        return [name for name in self.vision_provider_order_list if name in available]


@lru_cache
def get_settings() -> Settings:
    return Settings()
