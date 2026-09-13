"""Capability-aware AI routing with shared tool budget and portable state."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..config import Settings
from .base import (
    AllProvidersFailed,
    ChatResponse,
    NoCapableProvider,
    ProviderError,
    ToolCall,
    TransportFailureKind,
)
from .capabilities import ProviderCapabilities
from .provider import AIProvider
from .registry import build_registered_providers
from .retry import (
    ProviderRetryPolicy,
    RequestRetryState,
    is_cyclic_retryable_transport,
    transport_kind,
)
from .synthesis import build_fresh_synthesis_messages

logger = logging.getLogger(__name__)
ToolExecutor = Callable[[str, dict], Awaitable[str]]
_MAX_TOOL_CALLS_TOTAL = 8
_MAX_PARALLEL_TOOL_CALLS = 2


@dataclass(frozen=True)
class CompletionResult:
    content: str
    provider: str
    fallbacks: tuple[str, ...] = ()


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


@dataclass
class _RequestState:
    base_messages: list[dict]
    portable_messages: list[dict]
    tool_outputs: list[str]
    budget: _ToolBudget
    retry: RequestRetryState


def _capabilities(provider: object) -> ProviderCapabilities:
    value = getattr(provider, "capabilities", None)
    if isinstance(value, ProviderCapabilities):
        return value
    return ProviderCapabilities()


def _available(provider: object) -> bool:
    health = getattr(provider, "health", None)
    return health is None or health.available()


def _health_generation(provider: object) -> int | None:
    health = getattr(provider, "health", None)
    if health is None:
        return None
    return health.generation


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


def _flush_pending_health_error(provider: AIProvider, state: _RequestState) -> None:
    slot = state.retry.provider_state(provider.name)
    error = slot.pending_health_error
    if error is None:
        return
    health = getattr(provider, "health", None)
    generation = slot.pending_health_generation
    if health is not None and generation is not None:
        health.record_deferred_error(
            str(error),
            expected_generation=generation,
            status_code=error.status_code,
            retry_after=error.retry_after,
            transient=error.transient,
        )
    slot.pending_health_error = None
    slot.pending_health_generation = None


def _flush_all_pending_health_errors(
    candidates: tuple[AIProvider, ...],
    state: _RequestState,
    *,
    exclude: str | None = None,
) -> None:
    for provider in candidates:
        if provider.name == exclude:
            continue
        _flush_pending_health_error(provider, state)


def _candidate_eligible(provider: AIProvider, state: _RequestState) -> bool:
    return _available(provider) and state.retry.can_attempt(provider.name)


def _cyclic_indices(size: int, start: int):
    for offset in range(size):
        yield (start + offset) % size


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
        *,
        text_provider_order: tuple[str, ...] | None = None,
        vision_provider_order: tuple[str, ...] | None = None,
        retry_policy: ProviderRetryPolicy | None = None,
    ) -> None:
        provider_slots: set[tuple[str, str]] = set()
        for provider in providers:
            slot = (provider.name, _capabilities(provider).route)
            if slot in provider_slots:
                raise ValueError(
                    f"duplicate provider slot: name={slot[0]!r}, route={slot[1]!r}"
                )
            provider_slots.add(slot)

        self.providers = providers
        default_order = tuple(dict.fromkeys(provider.name for provider in providers))
        self.text_provider_order = (
            default_order if text_provider_order is None else text_provider_order
        )
        self.vision_provider_order = (
            default_order if vision_provider_order is None else vision_provider_order
        )
        self.max_tool_rounds = max_tool_rounds
        self.retry_policy = retry_policy or ProviderRetryPolicy()

    def provider_order(self, requires_vision: bool) -> tuple[str, ...]:
        if requires_vision:
            return self.vision_provider_order
        return self.text_provider_order

    def capable_providers(
        self,
        *,
        requires_vision: bool,
        image_count: int = 0,
    ) -> list[AIProvider]:
        out: list[AIProvider] = []
        for provider_name in self.provider_order(requires_vision):
            for provider in self.providers:
                if provider.name != provider_name:
                    continue
                if _capabilities(provider).accepts(
                    requires_vision=requires_vision,
                    image_count=image_count,
                ):
                    out.append(provider)
        return out

    def configured_provider_names(self, requires_vision: bool) -> tuple[str, ...]:
        providers = self.capable_providers(
            requires_vision=requires_vision,
            image_count=1 if requires_vision else 0,
        )
        return tuple(dict.fromkeys(provider.name for provider in providers))

    def max_supported_images(self) -> int:
        providers = self.capable_providers(requires_vision=True, image_count=1)
        return max((_capabilities(provider).max_images for provider in providers), default=0)

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
        *,
        requires_vision: bool = False,
        image_count: int = 0,
    ) -> CompletionResult:
        candidates = tuple(
            self.capable_providers(
                requires_vision=requires_vision,
                image_count=image_count,
            )
        )
        if not candidates:
            raise NoCapableProvider(
                "Không có AI provider nào phù hợp capability của request"
            )

        state = _RequestState(
            base_messages=deepcopy(messages),
            portable_messages=deepcopy(messages),
            tool_outputs=[],
            budget=_ToolBudget(),
            retry=RequestRetryState(self.retry_policy),
        )
        fallbacks: list[str] = []
        last_error: ProviderError | None = None
        cursor = 0
        previous_provider_name: str | None = None
        exclude_next_provider_name: str | None = None

        while not state.retry.request_transport_budget_exhausted():
            selected: tuple[int, AIProvider] | None = None
            for index in _cyclic_indices(len(candidates), cursor):
                provider = candidates[index]
                if provider.name == exclude_next_provider_name:
                    continue
                if _candidate_eligible(provider, state):
                    selected = (index, provider)
                    break
            if selected is None:
                break

            index, provider = selected
            exclude_next_provider_name = None
            if (
                previous_provider_name is not None
                and provider.name != previous_provider_name
            ):
                fallbacks.append(provider.name)
            previous_provider_name = provider.name

            try:
                text = await self._attempt_provider(
                    provider,
                    tools,
                    tool_executor,
                    state,
                    candidates=candidates,
                )
            except ProviderError as exc:
                last_error = exc
                if is_cyclic_retryable_transport(exc):
                    exclude_next_provider_name = provider.name
                    if not state.retry.can_attempt(provider.name):
                        _flush_pending_health_error(provider, state)
                else:
                    state.retry.block_provider(provider.name)
                    _record_error(provider, exc)
                logger.warning("AI provider %s lỗi: %s", provider.name, exc)
                cursor = (index + 1) % len(candidates)
                continue

            _record_success(provider)
            _flush_all_pending_health_errors(
                candidates,
                state,
                exclude=provider.name,
            )
            return CompletionResult(text, provider.name, tuple(fallbacks))

        _flush_all_pending_health_errors(candidates, state)
        message = str(last_error) if last_error else "Không có AI provider khả dụng"
        raise AllProvidersFailed(message, fallbacks=tuple(fallbacks))

    async def _attempt_provider(
        self,
        provider: AIProvider,
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
        state: _RequestState,
        *,
        candidates: tuple[AIProvider, ...],
    ) -> str:
        if state.budget.exhausted(self.max_tool_rounds):
            provider_messages = build_fresh_synthesis_messages(
                state.base_messages,
                state.tool_outputs,
            )
        else:
            provider_messages = deepcopy(state.portable_messages)

        already_plain = False
        last_error: ProviderError | None = None

        for pass_no in (0, 1):
            local_msgs = deepcopy(provider_messages)
            use_tools = (
                tools
                if (
                    provider.supports_tools
                    and pass_no == 0
                    and not state.budget.exhausted(self.max_tool_rounds)
                )
                else None
            )
            if use_tools is None and already_plain:
                break
            if use_tools is None:
                already_plain = True
            try:
                return await self._complete_with_provider(
                    provider,
                    local_msgs,
                    use_tools,
                    tool_executor,
                    state,
                    candidates=candidates,
                )
            except ProviderError as exc:
                last_error = exc
                if len(local_msgs) > len(provider_messages):
                    provider_messages = local_msgs
                if exc.unsupported_tools and provider.supports_tools:
                    provider.supports_tools = False
                    continue
                if exc.retry_without_tools:
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise ProviderError(
            f"{provider.name}: không thể hoàn tất provider-local recovery"
        )

    async def _chat_with_retry(
        self,
        provider: AIProvider,
        messages: list[dict],
        tools: list[dict] | None,
        state: _RequestState,
        *,
        candidates: tuple[AIProvider, ...],
    ) -> ChatResponse:
        while True:
            try:
                response = await provider.chat(messages, tools)
            except ProviderError as exc:
                kind = transport_kind(exc)
                if kind is None or not is_cyclic_retryable_transport(exc):
                    raise

                state.retry.record_transport_failure(
                    provider.name,
                    exc,
                    health_generation=_health_generation(provider),
                )
                if not state.retry.can_attempt(provider.name):
                    raise

                alternatives = [
                    candidate
                    for candidate in candidates
                    if candidate.name != provider.name
                    and _candidate_eligible(candidate, state)
                ]
                if kind == TransportFailureKind.READ_TIMEOUT and alternatives:
                    raise
                if not state.retry.consume_same_provider_retry(provider.name):
                    raise

                logger.warning(
                    "AI provider %s transient %s; retry same provider",
                    provider.name,
                    kind.value,
                )
                continue
            else:
                state.retry.record_chat_success(provider.name)
                return response

    async def _complete_with_provider(
        self,
        provider: AIProvider,
        messages: list[dict],
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
        state: _RequestState,
        *,
        candidates: tuple[AIProvider, ...],
    ) -> str:
        active_tools = tools
        while True:
            if active_tools and state.budget.exhausted(self.max_tool_rounds):
                messages[:] = build_fresh_synthesis_messages(
                    state.base_messages,
                    state.tool_outputs,
                )
                active_tools = None

            resp = await self._chat_with_retry(
                provider,
                messages,
                active_tools,
                state,
                candidates=candidates,
            )
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
            if not state.budget.can_execute(requested_calls, self.max_tool_rounds):
                state.budget.close()
                messages[:] = build_fresh_synthesis_messages(
                    state.base_messages,
                    state.tool_outputs,
                )
                active_tools = None
                continue

            state.budget.rounds += 1
            state.budget.calls += requested_calls
            messages.append(
                _assistant_tool_message(resp, include_provider_metadata=True)
            )
            state.portable_messages.append(
                _assistant_tool_message(resp, include_provider_metadata=False)
            )

            outputs = await _execute_tool_batch(resp.tool_calls, tool_executor)
            for tc, output in zip(resp.tool_calls, outputs, strict=True):
                normalized_output = str(output)[:6000]
                state.tool_outputs.append(normalized_output)
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": normalized_output,
                }
                messages.append(deepcopy(tool_message))
                state.portable_messages.append(tool_message)

            if state.budget.exhausted(self.max_tool_rounds):
                messages[:] = build_fresh_synthesis_messages(
                    state.base_messages,
                    state.tool_outputs,
                )
                active_tools = None


def _tool_call_message(
    tc: ToolCall,
    *,
    include_provider_metadata: bool = True,
) -> dict:
    out = {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.name, "arguments": _json_dumps(tc.arguments)},
    }
    if include_provider_metadata and tc.extra_content is not None:
        out["extra_content"] = deepcopy(tc.extra_content)
    return out


def _assistant_tool_message(
    resp: ChatResponse,
    *,
    include_provider_metadata: bool = True,
) -> dict:
    out = {
        "role": "assistant",
        "content": resp.content or "",
        "tool_calls": [
            _tool_call_message(
                tc,
                include_provider_metadata=include_provider_metadata,
            )
            for tc in resp.tool_calls
        ],
    }
    if include_provider_metadata:
        out.update(deepcopy(resp.assistant_metadata))
    return out


def _json_dumps(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)


def build_provider_router(settings: Settings) -> AIProviderRouter:
    text_order = tuple(settings.text_provider_order_list)
    vision_order = tuple(settings.vision_provider_order_list)
    registered_names = tuple(dict.fromkeys((*text_order, *vision_order)))
    providers = build_registered_providers(settings, registered_names)
    retry_policy = ProviderRetryPolicy(
        max_consecutive_failures=settings.provider_retry_max_consecutive_failures,
        max_failures_per_provider=settings.provider_retry_max_failures_per_provider,
        max_failures_per_request=settings.provider_retry_max_failures_per_request,
    )
    return AIProviderRouter(
        providers,
        max_tool_rounds=settings.max_tool_rounds,
        text_provider_order=text_order,
        vision_provider_order=vision_order,
        retry_policy=retry_policy,
    )
