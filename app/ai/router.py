"""Capability-aware AI router with shared tool budget and health-aware fallback."""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..config import Settings
from .base import (
    AllProvidersFailed,
    ChatResponse,
    NoCapableProvider,
    OpenAICompatProvider,
    ProviderError,
)
from .capabilities import ProviderCapabilities
from .cloudflare import make_cloudflare_provider
from .gemini import make_gemini_provider
from .groq import make_groq_provider
from .openrouter import make_openrouter_provider

logger = logging.getLogger(__name__)
ToolExecutor = Callable[[str, dict], Awaitable[str]]
_MAX_TOOL_CALLS_TOTAL = 8


@dataclass
class _ToolBudget:
    calls: int = 0
    rounds: int = 0


def _capabilities(provider: object) -> ProviderCapabilities:
    value = getattr(provider, "capabilities", None)
    if isinstance(value, ProviderCapabilities):
        return value
    return ProviderCapabilities()


def _available(provider: object) -> bool:
    health = getattr(provider, "health", None)
    return health is None or health.available()


def _record_success(provider: object) -> None:
    health = getattr(provider, "health", None)
    if health is not None:
        health.record_success()


def _record_error(provider: object, error: ProviderError) -> None:
    health = getattr(provider, "health", None)
    if health is not None:
        health.record_error(
            str(error),
            status_code=error.status_code,
            retry_after=error.retry_after,
            transient=error.transient,
        )


class AIProviderRouter:
    def __init__(self, providers: list[OpenAICompatProvider], max_tool_rounds: int = 4) -> None:
        self.providers = providers
        self.max_tool_rounds = max_tool_rounds
        self.last_fallbacks = 0

    def capable_providers(
        self,
        *,
        requires_vision: bool,
        image_count: int = 0,
    ) -> list[OpenAICompatProvider]:
        out: list[OpenAICompatProvider] = []
        for provider in self.providers:
            if _capabilities(provider).accepts(
                requires_vision=requires_vision,
                image_count=image_count,
            ):
                out.append(provider)
        return out

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
        *,
        requires_vision: bool = False,
        image_count: int = 0,
    ) -> tuple[str, str]:
        candidates = self.capable_providers(
            requires_vision=requires_vision,
            image_count=image_count,
        )
        if not candidates:
            raise NoCapableProvider("Không có provider phù hợp capability của request")

        last_error: ProviderError | None = None
        budget = _ToolBudget()
        attempted = 0
        self.last_fallbacks = 0

        for provider in candidates:
            if not _available(provider):
                continue
            if attempted:
                self.last_fallbacks += 1
            attempted += 1
            already_plain = False
            for pass_no in (0, 1):
                local_msgs = deepcopy(messages)
                use_tools = tools if (provider.supports_tools and pass_no == 0) else None
                if use_tools is None and already_plain:
                    break
                if use_tools is None:
                    already_plain = True
                try:
                    text = await self._complete_with_provider(
                        provider, local_msgs, use_tools, tool_executor, budget
                    )
                    _record_success(provider)
                    return text, provider.name
                except ProviderError as exc:
                    last_error = exc
                    _record_error(provider, exc)
                    logger.warning("Provider %s lỗi: %s", provider.name, exc)
                    if len(local_msgs) > len(messages):
                        messages = local_msgs
                    if exc.unsupported_tools and provider.supports_tools:
                        provider.supports_tools = False
                        continue
                    if exc.retry_without_tools:
                        continue
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = ProviderError(f"{provider.name}: {exc}")
                    _record_error(provider, last_error)
                    logger.warning("Provider %s lỗi không lường trước: %s", provider.name, exc)
                    if len(local_msgs) > len(messages):
                        messages = local_msgs
                    break

        if attempted == 0:
            raise AllProvidersFailed("Các provider phù hợp đang cooldown hoặc unavailable")
        raise AllProvidersFailed(str(last_error) if last_error else "Tất cả provider đều lỗi")

    async def _complete_with_provider(
        self,
        provider: OpenAICompatProvider,
        messages: list[dict],
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
        budget: _ToolBudget,
    ) -> str:
        for _round in range(self.max_tool_rounds + 1):
            resp = await provider.chat(messages, tools)
            if not resp.tool_calls:
                text = (resp.content or "").strip()
                if not text:
                    raise ProviderError(f"{provider.name}: model trả về nội dung rỗng")
                return text
            if not tools:
                raise ProviderError(f"{provider.name}: model gọi tool khi tools đã tắt")
            if budget.rounds >= self.max_tool_rounds:
                raise ProviderError(
                    f"{provider.name}: model gọi tool quá {self.max_tool_rounds} vòng",
                    retry_without_tools=True,
                )
            if budget.calls + len(resp.tool_calls) > _MAX_TOOL_CALLS_TOTAL:
                raise ProviderError(
                    f"{provider.name}: model yêu cầu quá {_MAX_TOOL_CALLS_TOTAL} tool call",
                    retry_without_tools=True,
                )
            budget.rounds += 1
            budget.calls += len(resp.tool_calls)
            messages.append(_assistant_tool_message(resp))
            for tc in resp.tool_calls:
                try:
                    output = await tool_executor(tc.name, tc.arguments)
                except Exception as exc:  # noqa: BLE001
                    output = f"Lỗi khi chạy tool '{tc.name}': {exc}"
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": str(output)[:6000]}
                )
        raise ProviderError(f"{provider.name}: vòng lặp tool kết thúc bất thường")


def _assistant_tool_message(resp: ChatResponse) -> dict:
    return {
        "role": "assistant",
        "content": resp.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": _json_dumps(tc.arguments)},
            }
            for tc in resp.tool_calls
        ],
    }


def _json_dumps(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)


def build_provider_router(settings: Settings) -> AIProviderRouter:
    providers: list[OpenAICompatProvider] = []

    text: dict[str, OpenAICompatProvider] = {}
    if settings.gemini_api_key and settings.gemini_model:
        text["gemini"] = make_gemini_provider(settings)
    if settings.groq_api_key and settings.groq_model:
        text["groq"] = make_groq_provider(settings)
    if settings.openrouter_api_key and settings.openrouter_model:
        text["openrouter"] = make_openrouter_provider(settings)
    if (
        settings.cloudflare_account_id
        and settings.cloudflare_api_token
        and settings.cloudflare_text_model
    ):
        text["cloudflare"] = make_cloudflare_provider(
            settings,
            name="cloudflare_text",
            model=settings.cloudflare_text_model,
            vision=False,
        )
    for slot_name in settings.text_provider_order_list:
        provider = text.get(slot_name)
        if provider is not None:
            providers.append(provider)

    if settings.vision_enabled:
        vision: dict[str, OpenAICompatProvider] = {}
        if settings.gemini_api_key and settings.gemini_vision_model:
            vision["gemini"] = make_gemini_provider(
                settings,
                name="gemini_vision",
                model=settings.gemini_vision_model,
                vision=True,
            )
        if settings.groq_api_key:
            models = settings.groq_vision_models_list
            if len(models) >= 1:
                vision["groq_qwen38"] = make_groq_provider(
                    settings,
                    name="groq_qwen38",
                    model=models[0],
                    vision=True,
                    max_images=3,
                )
            if len(models) >= 2:
                vision["groq_qwen36"] = make_groq_provider(
                    settings,
                    name="groq_qwen36",
                    model=models[1],
                    vision=True,
                    max_images=3,
                )
        if (
            settings.cloudflare_account_id
            and settings.cloudflare_api_token
            and settings.cloudflare_vision_model
        ):
            vision["cloudflare"] = make_cloudflare_provider(settings)
        for slot_name in settings.vision_provider_order_list:
            provider = vision.get(slot_name)
            if provider is not None:
                providers.append(provider)

    return AIProviderRouter(providers, max_tool_rounds=settings.max_tool_rounds)
