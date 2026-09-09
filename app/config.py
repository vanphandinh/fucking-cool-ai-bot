"""Cấu hình ứng dụng — đọc biến môi trường từ .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator, model_validator
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


def parse_unique_csv(raw: str | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").split(","):
        value = part.strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    bot_username: str = "FuckingCoolAIbot"
    allowed_group_ids: str = ""
    admin_ids: str = ""
    learn_group_id_mode: bool = False

    # Provider-specific AI credentials/models. Routing order stays provider-agnostic.
    bai_api_key: str = ""
    bai_text_model: str = "qwen3.8-flash"
    bai_request_timeout_sec: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    aurora_base_url: str = "http://aurora:8080/v1"
    aurora_api_key: str = ""
    aurora_model: str = "auto"
    aurora_request_timeout_sec: float = Field(default=90.0, gt=0, allow_inf_nan=False)
    text_provider_order: str = "bai,aurora"

    # Vision input. Each provider slot remains the source of truth for its limits.
    vision_enabled: bool = True
    bai_vision_model: str = "qwen3.8-flash"
    vision_provider_order: str = "bai"
    max_images_per_request: int = Field(default=1, ge=1)
    max_image_bytes: int = Field(default=8388608, ge=1)
    max_total_image_bytes: int = Field(default=12582912, ge=1)

    # Unified free/self-hosted-first search policy for both text and image search.
    search_backend: str = "auto"
    searxng_url: str = ""
    searxng_timeout_sec: float = Field(default=7.0, gt=0, allow_inf_nan=False)
    ddgs_timeout_sec: float = Field(default=8.0, gt=0, allow_inf_nan=False)
    search_total_timeout_sec: float = Field(default=15.0, gt=0, allow_inf_nan=False)
    search_circuit_failure_threshold: int = Field(default=3, ge=1, le=10)
    search_circuit_cooldown_sec: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    search_rate_limit_cooldown_sec: float = Field(default=180.0, gt=0, allow_inf_nan=False)
    search_cache_max_entries: int = Field(default=256, ge=1, le=4096)
    search_web_cache_ttl_sec: float = Field(default=60.0, ge=0, allow_inf_nan=False)
    search_image_cache_ttl_sec: float = Field(default=120.0, ge=0, allow_inf_nan=False)
    search_stale_cache_ttl_sec: float = Field(default=900.0, ge=0, allow_inf_nan=False)
    image_search_max_results: int = Field(default=4, ge=1, le=8)
    x_fetch_enabled: bool = True

    # URL rendering/extraction backend. Set CRAWL4AI_ENABLED=0 for instant rollback.
    crawl4ai_enabled: bool = True
    crawl4ai_url: str = "http://crawl4ai:11235"
    crawl4ai_api_token: str = ""
    crawl4ai_timeout_sec: float = Field(default=25.0, gt=0, allow_inf_nan=False)
    crawl4ai_max_chars: int = Field(default=12000, ge=1000, le=50000)

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

    @model_validator(mode="after")
    def _check_search_timeout_budget(self) -> "Settings":
        total = self.search_total_timeout_sec
        if self.searxng_timeout_sec > total:
            raise ValueError("SEARXNG_TIMEOUT_SEC không được lớn hơn SEARCH_TOTAL_TIMEOUT_SEC")
        if self.ddgs_timeout_sec > total:
            raise ValueError("DDGS_TIMEOUT_SEC không được lớn hơn SEARCH_TOTAL_TIMEOUT_SEC")
        crawl4ai_active = (
            self.crawl4ai_enabled
            and bool(self.crawl4ai_url.strip())
            and bool(self.crawl4ai_api_token.strip())
        )
        if crawl4ai_active and self.crawl4ai_timeout_sec >= self.question_timeout_sec:
            raise ValueError("CRAWL4AI_TIMEOUT_SEC phải nhỏ hơn QUESTION_TIMEOUT_SEC")
        return self

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
    def vision_provider_order_list(self) -> list[str]:
        return parse_unique_csv(self.vision_provider_order)


@lru_cache
def get_settings() -> Settings:
    return Settings()
