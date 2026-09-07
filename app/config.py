"""Cấu hình ứng dụng — đọc biến môi trường từ .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SEARCH_BACKENDS = ("ddgs", "searxng", "tavily")
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    bot_username: str = "FuckingCoolAIbot"
    allowed_group_ids: str = ""
    admin_ids: str = ""
    learn_group_id_mode: bool = False

    # Text pool — defaults intentionally unchanged.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"

    # Vision pool.
    vision_enabled: bool = True
    gemini_vision_model: str = "gemini-3.8-flash"
    groq_vision_models: str = "qwen/qwen3.8-27b,qwen/qwen3.6-27b"
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_vision_model: str = "@cf/google/gemma-4-26b-a4b-it"
    vision_provider_order: str = "gemini,groq_qwen38,groq_qwen36,cloudflare"
    max_images_per_request: int = Field(default=3, ge=1)
    max_image_bytes: int = Field(default=8388608, ge=1)
    max_total_image_bytes: int = Field(default=12582912, ge=1)

    search_backend: str = "ddgs"
    searxng_url: str = ""
    tavily_api_key: str = ""

    max_questions_per_min_per_user: int = Field(default=3, ge=0)
    max_context_turns: int = Field(default=10, ge=1)
    max_tool_rounds: int = Field(default=4, ge=0)
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
    def groq_vision_models_list(self) -> list[str]:
        return parse_csv(self.groq_vision_models)

    @property
    def vision_provider_order_list(self) -> list[str]:
        return parse_csv(self.vision_provider_order)

    @property
    def configured_provider_names(self) -> list[str]:
        names: list[str] = []
        if self.gemini_api_key:
            names.append("gemini")
        if self.groq_api_key:
            names.append("groq")
        if self.openrouter_api_key:
            names.append("openrouter")
        return names

    @property
    def configured_vision_provider_names(self) -> list[str]:
        if not self.vision_enabled:
            return []
        names: list[str] = []
        if self.gemini_api_key and self.gemini_vision_model:
            names.append("gemini")
        if self.groq_api_key:
            names.extend(["groq_qwen38", "groq_qwen36"][: len(self.groq_vision_models_list)])
        if self.cloudflare_account_id and self.cloudflare_api_token:
            names.append("cloudflare")
        return names


@lru_cache
def get_settings() -> Settings:
    return Settings()
