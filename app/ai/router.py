"""Capability-aware AI router with shared tool budget and health-aware fallback."""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..config import Settings
from .bai import make_bai_provider
from .base import (
    AllProvidersFailed,
    ChatResponse,
    NoCapableProvider,
    OpenAICompatProvider,
    ProviderError,
    ToolCall,
)
from .capabilities import ProviderCapabilities
from .cloudflare import make_cloudflare_provider
from .gemini import make_gemini_provider
from .groq import make_groq_provider
from .openrouter import make_openrouter_provider

logger = logging.getLogger(__name__)
ToolExecutor = Callable[[str, dict], Awaitable[str]]
_MAX_TOOL_CALLS_TOTAL = 8
_MAX_PARALLEL_TOOL_CALLS = 2
_PROVIDER_MESSAGE_FIELDS = ("reasoning_details", "reasoning", "reasoning_content")
_GEMINI_DUMMY_THOUGHT_SIGNATURE = "skip_thought_signature_validator"


@dataclass
class _ToolBudget:
    calls: int = 0
    rounds: int = 0
    closed: bool = False

    def exhausted(self, max_rounds: int) -> bool:
        return (
            self.closed
            or self.rounds >= max_rounds
            or self.calls >= _MAX_TOOL_CALLS_TOTAL
        )

    def can_execute(self, requested_calls: int, max_rounds: int) -> bool:
        return (
            not self.closed
            and self.rounds < max_rounds
            and self.calls + requested_calls <= _MAX_TOOL_CALLS_TOTAL
        )

    def close(self) -> None:
        self.closed = True


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


async def _execute_tool_batch(
    tool_calls: list[ToolCall],
    tool_executor: ToolExecutor,
) -> list[str]:
    semaphore = asyncio.Semaphore(_MAX_PARALLEL_TOOL_CALLS)

    async def run(tc: ToolCall) -> str:
        async with semaphore:
            try:
                return await tool_executor(tc.name, tc.arguments)
            except Exception as exc:  # noqa: BLE001
                return f"Lỗi khi chạy tool '{tc.name}': {exc}"

    return await asyncio.gather(*(run(tc) for tc in tool_calls))


class AIProviderRouter:
    def __init__(self, providers: list[OpenAICompatProvider], max_tool_rounds: int = 4) -> None:
        self.providers = providers
        self.max_tool_rounds = max_tool_rounds
        self._last_fallbacks: ContextVar[int] = ContextVar(
            f"ai_router_last_fallbacks_{id(self)}",
            default=0,
        )

    @property
    def last_fallbacks(self) -> int:
        """Fallback count for the current async request context."""
        return self._last_fallbacks.get()

    @last_fallbacks.setter
    def last_fallbacks(self, value: int) -> None:
        self._last_fallbacks.set(value)

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
            provider_messages = _messages_for_provider(messages, provider)
            for pass_no in (0, 1):
                local_msgs = deepcopy(provider_messages)
                use_tools = (
                    tools
                    if (
                        provider.supports_tools
                        and pass_no == 0
                        and not budget.exhausted(self.max_tool_rounds)
                    )
                    else None
                )
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
                    if len(local_msgs) > len(provider_messages):
                        provider_messages = local_msgs
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
                    if len(local_msgs) > len(provider_messages):
                        provider_messages = local_msgs
                    break
            messages = _portable_messages(provider_messages)

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
        active_tools = tools
        while True:
            if active_tools and budget.exhausted(self.max_tool_rounds):
                active_tools = None

            resp = await provider.chat(messages, active_tools)
            if not resp.tool_calls:
                text = (resp.content or "").strip()
                if not text:
                    raise ProviderError(f"{provider.name}: model trả về nội dung rỗng")
                return text
            if not active_tools:
                raise ProviderError(
                    f"{provider.name}: model gọi tool khi tools đã tắt",
                    transient=False,
                )

            requested_calls = len(resp.tool_calls)
            if not budget.can_execute(requested_calls, self.max_tool_rounds):
                budget.close()
                active_tools = None
                continue

            budget.rounds += 1
            budget.calls += requested_calls
            messages.append(_assistant_tool_message(resp))
            outputs = await _execute_tool_batch(resp.tool_calls, tool_executor)
            for tc, output in zip(resp.tool_calls, outputs, strict=True):
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": str(output)[:6000]}
                )

            if budget.exhausted(self.max_tool_rounds):
                active_tools = None


def _tool_call_message(tc: ToolCall) -> dict:
    out = {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.name, "arguments": _json_dumps(tc.arguments)},
    }
    if tc.extra_content is not None:
        out["extra_content"] = deepcopy(tc.extra_content)
    return out


def _assistant_tool_message(resp: ChatResponse) -> dict:
    out = {
        "role": "assistant",
        "content": resp.content or "",
        "tool_calls": [_tool_call_message(tc) for tc in resp.tool_calls],
    }
    out.update(deepcopy(resp.assistant_metadata))
    return out


def _messages_for_provider(messages: list[dict], provider: OpenAICompatProvider) -> list[dict]:
    """Prepare portable history for a specific fallback target.

    Gemini 3 validates a thought signature on historical function calls. Calls
    produced by another provider have no Gemini signature, so use Google's
    documented dummy signature only for those imported calls. Native Gemini
    tool turns keep the real signature captured in ``extra_content``.
    """
    out = _portable_messages(messages)
    if not provider.name.startswith("gemini"):
        return out
    for message in out:
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            extra_content = tool_call.setdefault("extra_content", {})
            if not isinstance(extra_content, dict):
                extra_content = {}
                tool_call["extra_content"] = extra_content
            google = extra_content.setdefault("google", {})
            if not isinstance(google, dict):
                google = {}
                extra_content["google"] = google
            google.setdefault("thought_signature", _GEMINI_DUMMY_THOUGHT_SIGNATURE)
    return out


def _portable_messages(messages: list[dict]) -> list[dict]:
    """Strip provider-specific metadata before cross-provider fallback."""
    out = deepcopy(messages)
    for message in out:
        for field_name in _PROVIDER_MESSAGE_FIELDS:
            message.pop(field_name, None)
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for tool_call in tool_calls:
            if isinstance(tool_call, dict):
                tool_call.pop("extra_content", None)
    return out


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
        "bai" in settings.text_provider_order_list
        and settings.bai_api_key
        and settings.bai_text_model
    ):
        text["bai"] = make_bai_provider(settings)
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
            "bai" in settings.vision_provider_order_list
            and settings.bai_api_key
            and settings.bai_vision_model
        ):
            vision["bai"] = make_bai_provider(
                settings,
                name="bai_vision",
                vision=True,
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
