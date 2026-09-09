"""Capability-aware B.AI router with shared tool budget and health handling."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..config import Settings
from .bai import make_bai_provider
from .base import (
    AllProvidersFailed,
    ChatResponse,
    NoCapableProvider,
    ProviderError,
    ToolCall,
)
from .capabilities import ProviderCapabilities
from .provider import AIProvider
from .synthesis import build_fresh_synthesis_messages

logger = logging.getLogger(__name__)
ToolExecutor = Callable[[str, dict], Awaitable[str]]
_MAX_TOOL_CALLS_TOTAL = 8
_MAX_PARALLEL_TOOL_CALLS = 2


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
    def __init__(
        self,
        providers: list[AIProvider],
        max_tool_rounds: int = 4,
    ) -> None:
        self.providers = providers
        self.max_tool_rounds = max_tool_rounds

    def capable_providers(
        self,
        *,
        requires_vision: bool,
        image_count: int = 0,
    ) -> list[AIProvider]:
        out: list[AIProvider] = []
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
            raise NoCapableProvider(
                "Không có B.AI provider phù hợp capability của request"
            )

        provider = candidates[0]
        if not _available(provider):
            raise AllProvidersFailed("B.AI provider đang cooldown hoặc unavailable")

        budget = _ToolBudget()
        synthesis_base_messages = deepcopy(messages)
        tool_outputs: list[str] = []
        provider_messages = deepcopy(messages)
        already_plain = False
        last_error: ProviderError | None = None

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
                    provider,
                    local_msgs,
                    use_tools,
                    tool_executor,
                    budget,
                    synthesis_base_messages,
                    tool_outputs,
                )
                _record_success(provider)
                return text, provider.name
            except ProviderError as exc:
                last_error = exc
                _record_error(provider, exc)
                logger.warning("B.AI provider %s lỗi: %s", provider.name, exc)
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
                logger.warning(
                    "B.AI provider %s lỗi không lường trước: %s",
                    provider.name,
                    exc,
                )
                break

        message = str(last_error) if last_error else "B.AI không thể hoàn tất request"
        raise AllProvidersFailed(message)

    async def _complete_with_provider(
        self,
        provider: AIProvider,
        messages: list[dict],
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
        budget: _ToolBudget,
        synthesis_base_messages: list[dict],
        tool_outputs: list[str],
    ) -> str:
        active_tools = tools
        while True:
            if active_tools and budget.exhausted(self.max_tool_rounds):
                messages[:] = build_fresh_synthesis_messages(
                    synthesis_base_messages,
                    tool_outputs,
                )
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
                messages[:] = build_fresh_synthesis_messages(
                    synthesis_base_messages,
                    tool_outputs,
                )
                active_tools = None
                continue

            budget.rounds += 1
            budget.calls += requested_calls
            messages.append(_assistant_tool_message(resp))
            outputs = await _execute_tool_batch(resp.tool_calls, tool_executor)
            for tc, output in zip(resp.tool_calls, outputs, strict=True):
                normalized_output = str(output)[:6000]
                tool_outputs.append(normalized_output)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": normalized_output,
                    }
                )

            if budget.exhausted(self.max_tool_rounds):
                messages[:] = build_fresh_synthesis_messages(
                    synthesis_base_messages,
                    tool_outputs,
                )
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


def _json_dumps(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)


def build_provider_router(settings: Settings) -> AIProviderRouter:
    providers: list[AIProvider] = []

    if settings.bai_api_key and settings.bai_text_model:
        providers.append(make_bai_provider(settings))

    vision_configured = (
        settings.vision_enabled
        and settings.bai_api_key
        and settings.bai_vision_model
    )
    if vision_configured:
        providers.append(
            make_bai_provider(
                settings,
                name="bai_vision",
                vision=True,
            )
        )

    return AIProviderRouter(providers, max_tool_rounds=settings.max_tool_rounds)
