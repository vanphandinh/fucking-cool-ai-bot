"""Capability-aware AI routing with bounded target recovery and portable state."""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
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
from .recovery import HealthScope, RecoveryAction, classify_recovery
from .registry import build_registered_providers
from .retry import (
    ProviderRetryPolicy,
    RequestRetryState,
    is_cyclic_retryable_transport,
    transport_kind,
)
from .scoped_health import ScopedHealthRegistry
from .synthesis import build_fresh_synthesis_messages
from .target import ProviderTargetIdentity, provider_target_identity

logger = logging.getLogger(__name__)
ToolExecutor = Callable[[str, dict], Awaitable[str]]
_MAX_TOOL_CALLS_TOTAL = 8
_MAX_PARALLEL_TOOL_CALLS = 2
_HEALTH_SCOPES = (
    HealthScope.FAMILY,
    HealthScope.CREDENTIAL,
    HealthScope.MODEL,
    HealthScope.ENTITLEMENT,
    HealthScope.TARGET,
)


@dataclass(frozen=True)
class CompletionResult:
    content: str
    provider: str
    fallbacks: tuple[str, ...] = ()
    target_rotations: int = 0
    model_rotations: int = 0
    credential_failovers: int = 0


@dataclass(frozen=True)
class _HealthGenerationSnapshot:
    adapter_generation: int | None
    scoped_generations: tuple[int, ...]


@dataclass
class _ToolBudget:
    calls: int = 0
    rounds: int = 0
    closed: bool = False

    def exhausted(self, max_rounds: int) -> bool:
        return self.closed or self.rounds >= max_rounds or self.calls >= _MAX_TOOL_CALLS_TOTAL

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
    unsupported_tool_models: set[tuple[str, str]]
    successful_health_snapshot: _HealthGenerationSnapshot | None


def _capabilities(provider: object) -> ProviderCapabilities:
    value = getattr(provider, "capabilities", None)
    if isinstance(value, ProviderCapabilities):
        return value
    return ProviderCapabilities()


def _adapter_available(provider: object) -> bool:
    health = getattr(provider, "health", None)
    return health is None or health.available()


def _health_generation(provider: object) -> int | None:
    health = getattr(provider, "health", None)
    if health is None:
        return None
    return health.generation


def _record_adapter_success(
    provider: object,
    *,
    expected_generation: int | None,
) -> None:
    health = getattr(provider, "health", None)
    if health is not None and expected_generation is not None:
        health.record_success_if_generation(expected_generation)


def _record_adapter_error(provider: object, error: ProviderError) -> None:
    health = getattr(provider, "health", None)
    if health is not None:
        health.record_error(
            str(error),
            status_code=error.status_code,
            retry_after=error.retry_after,
            transient=error.transient,
        )


def _flush_pending_family_health_error(
    candidates: tuple[AIProvider, ...],
    state: _RequestState,
    family: str,
) -> None:
    slot = state.retry.family_state(family)
    error = slot.pending_health_error
    if error is None:
        return

    provider: AIProvider | None = None
    if slot.pending_health_target_id is not None:
        provider = next(
            (
                candidate
                for candidate in candidates
                if provider_target_identity(candidate).target_id
                == slot.pending_health_target_id
            ),
            None,
        )
    if provider is None:
        provider = next((candidate for candidate in candidates if candidate.name == family), None)

    health = getattr(provider, "health", None) if provider is not None else None
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
    slot.pending_health_target_id = None


def _flush_all_pending_health_errors(
    candidates: tuple[AIProvider, ...],
    state: _RequestState,
    *,
    exclude_family: str | None = None,
) -> None:
    families = tuple(dict.fromkeys(candidate.name for candidate in candidates))
    for family in families:
        if family == exclude_family:
            continue
        _flush_pending_family_health_error(candidates, state, family)


def _cyclic_indices(size: int, start: int):
    for offset in range(size):
        yield (start + offset) % size


async def _execute_tool_batch(
    tool_calls: list[ToolCall],
    tool_executor: ToolExecutor,
    *,
    operations=None,
    retain: Callable[[int, str], None] | None = None,
) -> list[str]:
    semaphore = asyncio.Semaphore(_MAX_PARALLEL_TOOL_CALLS)

    async def run(index: int, tc: ToolCall) -> str:
        async with semaphore:
            if operations is not None:
                await operations.control.checkpoint()
            budget_context = (
                operations.budget(operations.tool_timeout)
                if operations is not None
                else nullcontext()
            )
            try:
                with budget_context:
                    output = await tool_executor(tc.name, tc.arguments)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                output = f"Lỗi khi chạy tool '{tc.name}': {exc}"
            normalized = str(output)[:6000]
            if retain is not None:
                retain(index, normalized)
            return normalized

    tasks = [
        asyncio.create_task(run(index, tc), name=f"tool-batch:{tc.name}:{index}")
        for index, tc in enumerate(tool_calls)
    ]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        raise


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
        target_ids: set[str] = set()
        for provider in providers:
            identity = provider_target_identity(provider)
            if identity.target_id in target_ids:
                raise ValueError(f"duplicate provider target: {identity.target_id!r}")
            target_ids.add(identity.target_id)

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
        self.scoped_health = ScopedHealthRegistry()

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

    def _candidate_eligible(self, provider: AIProvider, state: _RequestState) -> bool:
        identity = provider_target_identity(provider)
        return (
            _adapter_available(provider)
            and self.scoped_health.available(identity)
            and state.retry.can_attempt_target(identity.family, identity.target_id)
        )

    def _has_eligible_sibling(
        self,
        candidates: tuple[AIProvider, ...],
        state: _RequestState,
        identity: ProviderTargetIdentity,
    ) -> bool:
        return any(
            sibling_identity.family == identity.family
            and sibling_identity.target_id != identity.target_id
            and self._candidate_eligible(candidate, state)
            for candidate in candidates
            for sibling_identity in (provider_target_identity(candidate),)
        )

    def _health_snapshot(
        self,
        provider: AIProvider,
        identity: ProviderTargetIdentity,
    ) -> _HealthGenerationSnapshot:
        return _HealthGenerationSnapshot(
            adapter_generation=_health_generation(provider),
            scoped_generations=tuple(
                self.scoped_health.generation(scope, identity)
                for scope in _HEALTH_SCOPES
            ),
        )

    def _record_scoped_success(
        self,
        identity: ProviderTargetIdentity,
        *,
        expected_generations: tuple[int, ...],
    ) -> None:
        for scope, expected_generation in zip(
            _HEALTH_SCOPES,
            expected_generations,
            strict=True,
        ):
            self.scoped_health.record_success_if_generation(
                scope,
                identity,
                expected_generation=expected_generation,
            )

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
        *,
        requires_vision: bool = False,
        image_count: int = 0,
        operations=None,
    ) -> CompletionResult:
        candidates = tuple(
            self.capable_providers(
                requires_vision=requires_vision,
                image_count=image_count,
            )
        )
        if not candidates:
            raise NoCapableProvider("Không có AI provider nào phù hợp capability của request")

        state = _RequestState(
            base_messages=deepcopy(messages),
            portable_messages=deepcopy(messages),
            tool_outputs=[],
            budget=_ToolBudget(),
            retry=RequestRetryState(self.retry_policy),
            unsupported_tool_models=set(),
            successful_health_snapshot=None,
        )
        fallbacks: list[str] = []
        last_error: ProviderError | None = None
        cursor = 0
        previous_identity: ProviderTargetIdentity | None = None
        exclude_next_family: str | None = None
        target_rotations = 0
        model_rotations = 0
        credential_failovers = 0

        while not state.retry.request_transport_budget_exhausted():
            selected: tuple[int, AIProvider] | None = None
            for index in _cyclic_indices(len(candidates), cursor):
                provider = candidates[index]
                identity = provider_target_identity(provider)
                if identity.family == exclude_next_family:
                    continue
                if self._candidate_eligible(provider, state):
                    selected = (index, provider)
                    break
            if selected is None:
                break

            index, provider = selected
            identity = provider_target_identity(provider)
            exclude_next_family = None
            if previous_identity is not None:
                if identity.family != previous_identity.family:
                    fallbacks.append(identity.family)
                elif identity.target_id != previous_identity.target_id:
                    target_rotations += 1
                    if identity.model != previous_identity.model:
                        model_rotations += 1
                    if identity.credential_id != previous_identity.credential_id:
                        credential_failovers += 1
            previous_identity = identity

            try:
                text = await self._attempt_provider(
                    provider,
                    tools,
                    tool_executor,
                    state,
                    candidates=candidates,
                    operations=operations,
                )
            except ProviderError as exc:
                last_error = exc
                if is_cyclic_retryable_transport(exc):
                    exclude_next_family = identity.family
                    if not state.retry.can_attempt(identity.family):
                        _flush_pending_family_health_error(
                            candidates, state, identity.family
                        )
                else:
                    decision = classify_recovery(identity, exc)
                    _record_adapter_error(provider, exc)
                    self.scoped_health.record_effect(
                        decision.health_scope,
                        identity,
                        str(exc),
                        effect=decision.health_effect,
                        retry_after=exc.retry_after,
                    )
                    state.retry.block_target(identity.target_id)
                    if decision.action == RecoveryAction.ROTATE_TARGET:
                        if not (
                            self._has_eligible_sibling(candidates, state, identity)
                            and state.retry.consume_recovery_hop()
                        ):
                            state.retry.block_family(identity.family)
                    else:
                        state.retry.block_family(identity.family)
                logger.warning(
                    "AI target %s lỗi: %s",
                    identity.target_id,
                    exc,
                )
                cursor = (index + 1) % len(candidates)
                continue

            health_snapshot = state.successful_health_snapshot
            if health_snapshot is None:
                raise RuntimeError("successful provider attempt missing health snapshot")
            _record_adapter_success(
                provider,
                expected_generation=health_snapshot.adapter_generation,
            )
            self._record_scoped_success(
                identity,
                expected_generations=health_snapshot.scoped_generations,
            )
            _flush_all_pending_health_errors(
                candidates,
                state,
                exclude_family=identity.family,
            )
            return CompletionResult(
                text,
                identity.family,
                tuple(fallbacks),
                target_rotations,
                model_rotations,
                credential_failovers,
            )

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
        operations=None,
    ) -> str:
        if state.budget.exhausted(self.max_tool_rounds):
            provider_messages = build_fresh_synthesis_messages(
                state.base_messages,
                state.tool_outputs,
            )
        else:
            provider_messages = deepcopy(state.portable_messages)

        identity = provider_target_identity(provider)
        already_plain = False
        last_error: ProviderError | None = None

        for pass_no in (0, 1):
            local_msgs = deepcopy(provider_messages)
            use_tools = (
                tools
                if (
                    provider.supports_tools
                    and identity.model_key not in state.unsupported_tool_models
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
                    operations=operations,
                )
            except ProviderError as exc:
                last_error = exc
                if len(local_msgs) > len(provider_messages):
                    provider_messages = local_msgs
                if exc.unsupported_tools and provider.supports_tools:
                    provider.supports_tools = False
                    state.unsupported_tool_models.add(identity.model_key)
                    continue
                if exc.retry_without_tools:
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise ProviderError(f"{provider.name}: không thể hoàn tất provider-local recovery")

    async def _chat_with_retry(
        self,
        provider: AIProvider,
        messages: list[dict],
        tools: list[dict] | None,
        state: _RequestState,
        *,
        candidates: tuple[AIProvider, ...],
        operations=None,
    ) -> ChatResponse:
        identity = provider_target_identity(provider)
        while True:
            health_snapshot = self._health_snapshot(provider, identity)
            try:
                try:
                    if operations is None:
                        response = await provider.chat(messages, tools)
                    else:
                        response = await operations.run(
                            f"AI {provider.name}",
                            lambda: provider.chat(messages, tools),
                            timeout_sec=operations.ai_timeout,
                        )
                except TimeoutError:
                    raise ProviderError(
                        f"{provider.name}: AI attempt timeout",
                        transient=True,
                        transport_kind=TransportFailureKind.READ_TIMEOUT,
                    ) from None
            except ProviderError as exc:
                kind = transport_kind(exc)
                if kind is None or not is_cyclic_retryable_transport(exc):
                    raise

                state.retry.record_transport_failure(
                    identity.family,
                    exc,
                    health_generation=_health_generation(provider),
                    target_id=identity.target_id,
                )
                if not state.retry.can_attempt(identity.family):
                    raise

                alternatives = [
                    candidate
                    for candidate in candidates
                    if provider_target_identity(candidate).family != identity.family
                    and self._candidate_eligible(candidate, state)
                ]
                if kind == TransportFailureKind.READ_TIMEOUT and alternatives:
                    raise
                if not state.retry.consume_same_provider_retry(identity.family):
                    raise

                logger.warning(
                    "AI target %s transient %s; retry exact target",
                    identity.target_id,
                    kind.value,
                )
                continue
            else:
                state.retry.record_chat_success(identity.family)
                state.successful_health_snapshot = health_snapshot
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
        operations=None,
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
                operations=operations,
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
            messages.append(_assistant_tool_message(resp, include_provider_metadata=True))
            state.portable_messages.append(
                _assistant_tool_message(resp, include_provider_metadata=False)
            )

            base_index = len(state.tool_outputs)
            state.tool_outputs.extend([""] * requested_calls)

            def retain(index: int, output: str) -> None:
                state.tool_outputs[base_index + index] = output

            outputs = await _execute_tool_batch(
                resp.tool_calls,
                tool_executor,
                operations=operations,
                retain=retain,
            )
            for tc, output in zip(resp.tool_calls, outputs, strict=True):
                tool_message = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": output,
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
            _tool_call_message(tc, include_provider_metadata=include_provider_metadata)
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
        max_recovery_hops_per_request=settings.provider_recovery_max_hops_per_request,
    )
    return AIProviderRouter(
        providers,
        max_tool_rounds=settings.max_tool_rounds,
        text_provider_order=text_order,
        vision_provider_order=vision_order,
        retry_policy=retry_policy,
    )
