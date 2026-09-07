"""Cấu hình ứng dụng — đọc biến môi trường từ .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SEARCH_BACKENDS = ("ddgs", "searxng", "tavily")
_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def parse_csv_ints(raw: str | None) -> list[int]:
    """Parse chuỗi '1,2,-1003' thành list[int] (bỏ qua phần rác)."""
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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    bot_token: str = ""
    bot_username: str = "FuckingCoolAIbot"
    # Danh sách chat_id group được phép (cách nhau bằng dấu phẩy), vd "-100111,-100222"
    allowed_group_ids: str = ""
    # user_id admin được dùng lệnh /status
    admin_ids: str = ""
    # Bật =1 để bot KHÔNG rời group lạ, chỉ log chat_id (dùng lần đầu lấy id group)
    learn_group_id_mode: bool = False

    # --- AI providers (free tier) ---
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"

    # --- Web search ---
    # ddgs | searxng | tavily
    search_backend: str = "ddgs"
    searxng_url: str = ""
    tavily_api_key: str = ""

    # --- Giới hạn sử dụng ---
    max_questions_per_min_per_user: int = Field(default=3, ge=0)
    max_context_turns: int = Field(default=10, ge=1)
    max_tool_rounds: int = Field(default=4, ge=0)
    request_timeout_sec: float = Field(default=60.0, gt=0)

    log_level: str = "INFO"

    # ---------- Validate cấu hình (fail-fast khi gõ sai) ----------
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

    # ---------- Các tiện ích ----------
    @property
    def allowed_group_ids_list(self) -> list[int]:
        return parse_csv_ints(self.allowed_group_ids)

    @property
    def admin_ids_list(self) -> list[int]:
        return parse_csv_ints(self.admin_ids)

    @property
    def bot_username_clean(self) -> str:
        """Tên bot dạng thường, không '@' — để so khớp mention."""
        return self.bot_username.lower().lstrip("@")

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
