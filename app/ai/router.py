"""Capability-aware AI routing with bounded target recovery and portable state."""

from __future__ import annotations

import asyncio
import logging
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Awaitable, Callable

from ..config import Settings
from .base import (
    AllProvidersFailed,
    NoCapableProvider,
    ProviderError,
    TransportFailureKind,
)
from .contracts import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
)
from .capabilities import ProviderCapabilities
from .provider import AIProvider
from .recovery import HealthEffect, HealthScope, RecoveryAction, classify_recovery
from .target_builder import build_provider_targets
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
_MAX_TOOL_CALLS_TOTAL = 12
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


class _TargetBecameUnavailable(Exception):
    """Signal scheduler rotation when shared health changes before a network call."""


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
    base_request: ChatRequest
    portable_messages: list[ChatMessage]
    tool_outputs: list[str]
    budget: _ToolBudget
    retry: RequestRetryState
    unsupported_tool_models: set[tuple[str, str, str]]
    successful_health_snapshot: _HealthGenerationSnapshot | None


def _capabilities(provider: AIProvider) -> ProviderCapabilities:
    return provider.spec.capabilities


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


def _record_adapter_effect(
    provider: object,
    message: str,
    *,
    effect: HealthEffect,
    retry_after: float | None,
) -> None:
    health = getattr(provider, "health", None)
    if health is None:
        return
    if effect == HealthEffect.DISABLE:
        health.record_disabled(message)
        return
    if effect == HealthEffect.COOLDOWN:
        health.record_cooldown(message, retry_after=retry_after)
        return
    if effect == HealthEffect.TRANSIENT:
        health.record_transient_error(message)
        return
    if effect == HealthEffect.RECORD_ONLY:
        health.record_observation(message)
        return
    raise ValueError(f"unknown health effect: {effect!r}")


def _flush_pending_family_health_errors(
    candidates: tuple[AIProvider, ...],
    state: _RequestState,
    family: str,
) -> None:
    incidents = state.retry.pending_health_incidents(family)
    for incident in incidents:
        provider = next(
            (
                candidate
                for candidate in candidates
                if provider_target_identity(candidate).target_id == incident.target_id
            ),
            None,
        )
        if provider is not None:
            health = getattr(provider, "health", None)
            if health is not None and incident.health_barrier_generation is not None:
                error = incident.error
                health.record_deferred_error(
                    str(error),
                    expected_barrier_generation=incident.health_barrier_generation,
                    status_code=error.status_code,
                    retry_after=error.retry_after,
                    transient=error.transient,
                )
        state.retry.discard_pending_health_incident(family, incident.target_id)


def _flush_all_pending_health_errors(
    candidates: tuple[AIProvider, ...],
    state: _RequestState,
    *,
    exclude_family: str | None = None,
) -> None:
    families = tuple(
        dict.fromkeys(provider_target_identity(candidate).family for candidate in candidates)
    )
    for family in families:
        if family == exclude_family:
            continue
        _flush_pending_family_health_errors(candidates, state, family)


def _cyclic_indices(size: int, start: int):
    for offset in range(size):
        yield (start + offset) % size


async def _execute_tool_batch(
    tool_calls: tuple[ToolCallPart, ...],
    tool_executor: ToolExecutor,
    *,
    operations=None,
    retain: Callable[[int, str], None] | None = None,
) -> list[str]:
    semaphore = asyncio.Semaphore(_MAX_PARALLEL_TOOL_CALLS)

    async def run(index: int, tc: ToolCallPart) -> str:
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
                    output = await tool_executor(tc.name, dict(tc.arguments))
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
        max_tool_rounds: int = 5,
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
        default_order = tuple(
            dict.fromkeys(provider_target_identity(provider).family for provider in providers)
        )
        self.text_provider_order = (
            default_order if text_provider_order is None else text_provider_order
        )
        self.vision_provider_order = (
            default_order if vision_provider_order is None else vision_provider_order
        )
        self.max_tool_rounds = max_tool_rounds
        self.retry_policy = retry_policy or ProviderRetryPolicy()
        self.scoped_health = ScopedHealthRegistry()
        self._unsupported_tool_models: set[tuple[str, str, str]] = set()

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
                if provider_target_identity(provider).family != provider_name:
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
        return tuple(
            dict.fromkeys(provider_target_identity(provider).family for provider in providers)
        )

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
        for candidate in candidates:
            sibling_identity = provider_target_identity(candidate)
            if sibling_identity.family != identity.family:
                continue
            if sibling_identity.target_id == identity.target_id:
                continue
            if self._candidate_eligible(candidate, state):
                return True
        return False

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
        request: ChatRequest,
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

        portable_base_messages = tuple(message.portable() for message in request.messages)
        portable_base_request = ChatRequest(
            messages=portable_base_messages,
            tools=request.tools,
        )
        state = _RequestState(
            base_request=portable_base_request,
            portable_messages=list(portable_base_messages),
            tool_outputs=[],
            budget=_ToolBudget(),
            retry=RequestRetryState(self.retry_policy),
            unsupported_tool_models=self._unsupported_tool_models,
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
                if exclude_next_family == identity.family:
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
                    request.tools,
                    tool_executor,
                    state,
                    candidates=candidates,
                    operations=operations,
                )
            except _TargetBecameUnavailable:
                cursor = (index + 1) % len(candidates)
                continue
            except ProviderError as exc:
                last_error = exc
                recovery_budget_exhausted = False
                if is_cyclic_retryable_transport(exc):
                    exclude_next_family = identity.family
                    if not state.retry.can_attempt(identity.family):
                        _flush_pending_family_health_errors(
                            candidates, state, identity.family
                        )
                else:
                    state.retry.discard_pending_health_incident(
                        identity.family,
                        identity.target_id,
                    )
                    decision = classify_recovery(provider.spec, exc)
                    _record_adapter_effect(
                        provider,
                        str(exc),
                        effect=decision.health_effect,
                        retry_after=exc.retry_after,
                    )
                    self.scoped_health.record_effect(
                        decision.health_scope,
                        identity,
                        str(exc),
                        effect=decision.health_effect,
                        retry_after=exc.retry_after,
                    )
                    state.retry.block_target(identity.target_id)
                    if decision.action == RecoveryAction.ROTATE_TARGET:
                        if not self._has_eligible_sibling(candidates, state, identity):
                            state.retry.block_family(identity.family)
                    else:
                        state.retry.block_family(identity.family)

                    recovery_target_available = any(
                        self._candidate_eligible(candidate, state)
                        for candidate in candidates
                    )
                    recovery_budget_exhausted = (
                        recovery_target_available
                        and not state.retry.consume_recovery_hop()
                    )
                logger.warning(
                    "AI target %s lỗi: %s",
                    identity.target_id,
                    exc,
                )
                if recovery_budget_exhausted:
                    break
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
            _flush_all_pending_health_errors(candidates, state)
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
        raise AllProvidersFailed(
            message,
            fallbacks=tuple(fallbacks),
            target_rotations=target_rotations,
            model_rotations=model_rotations,
            credential_failovers=credential_failovers,
        )

    async def _attempt_provider(
        self,
        provider: AIProvider,
        tools: tuple[ToolDefinition, ...],
        tool_executor: ToolExecutor,
        state: _RequestState,
        *,
        candidates: tuple[AIProvider, ...],
        operations=None,
    ) -> str:
        identity = provider_target_identity(provider)
        tool_capability_key = (identity.family, identity.route, identity.model)
        structured_tools_available = (
            _capabilities(provider).supports_tools
            and tool_capability_key not in state.unsupported_tool_models
        )
        if (
            state.budget.exhausted(self.max_tool_rounds)
            or (state.tool_outputs and not structured_tools_available)
        ):
            provider_messages = build_fresh_synthesis_messages(
                state.base_request.messages,
                state.tool_outputs,
            )
        else:
            provider_messages = list(state.portable_messages)

        already_plain = False
        last_error: ProviderError | None = None

        for pass_no in (0, 1):
            local_msgs = list(provider_messages)
            use_tools = (
                tools
                if (
                    structured_tools_available
                    and pass_no == 0
                    and not state.budget.exhausted(self.max_tool_rounds)
                )
                else ()
            )
            if not use_tools and already_plain:
                break
            if not use_tools:
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
                if exc.unsupported_tools:
                    state.unsupported_tool_models.add(tool_capability_key)
                    structured_tools_available = False
                    if state.tool_outputs:
                        provider_messages = build_fresh_synthesis_messages(
                            state.base_request.messages,
                            state.tool_outputs,
                        )
                    continue
                if exc.retry_without_tools:
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise ProviderError(f"{identity.family}: không thể hoàn tất provider-local recovery")

    async def _chat_with_retry(
        self,
        provider: AIProvider,
        messages: list[ChatMessage],
        tools: tuple[ToolDefinition, ...],
        state: _RequestState,
        *,
        candidates: tuple[AIProvider, ...],
        operations=None,
    ) -> ChatResponse:
        identity = provider_target_identity(provider)

        async def send_once() -> tuple[ChatResponse, _HealthGenerationSnapshot]:
            if not self._candidate_eligible(provider, state):
                raise _TargetBecameUnavailable
            health_snapshot = self._health_snapshot(provider, identity)
            response = await provider.chat(
                ChatRequest(messages=tuple(messages), tools=tools)
            )
            return response, health_snapshot

        while True:
            try:
                try:
                    if operations is None:
                        response, health_snapshot = await send_once()
                    else:
                        response, health_snapshot = await operations.run(
                            f"AI {identity.family}",
                            send_once,
                            timeout_sec=operations.ai_timeout,
                        )
                except TimeoutError:
                    raise ProviderError(
                        f"{identity.family}: AI attempt timeout",
                        transient=True,
                        transport_kind=TransportFailureKind.READ_TIMEOUT,
                    ) from None
            except ProviderError as exc:
                kind = transport_kind(exc)
                if kind is None or not is_cyclic_retryable_transport(exc):
                    raise

                health = getattr(provider, "health", None)
                barrier_generation = (
                    health.observe_deferred_error() if health is not None else None
                )
                state.retry.record_transport_failure(
                    identity.family,
                    exc,
                    health_barrier_generation=barrier_generation,
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
                state.retry.record_chat_success(
                    identity.family,
                    target_id=identity.target_id,
                )
                state.successful_health_snapshot = health_snapshot
                return response

    async def _complete_with_provider(
        self,
        provider: AIProvider,
        messages: list[ChatMessage],
        tools: tuple[ToolDefinition, ...],
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
                    state.base_request.messages,
                    state.tool_outputs,
                )
                active_tools = ()

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
                    raise ProviderError(
                    f"{provider_target_identity(provider).family}: model trả về nội dung rỗng"
                )
                return text
            if not active_tools:
                raise ProviderError(
                    f"{provider_target_identity(provider).family}: model gọi tool khi tools đã tắt",
                    transient=False,
                )

            requested_calls = len(resp.tool_calls)
            if not state.budget.can_execute(requested_calls, self.max_tool_rounds):
                state.budget.close()
                messages[:] = build_fresh_synthesis_messages(
                    state.base_request.messages,
                    state.tool_outputs,
                )
                active_tools = ()
                continue

            state.budget.rounds += 1
            state.budget.calls += requested_calls
            assistant_parts = (
                ((TextPart(resp.content),) if resp.content else ())
                + tuple(resp.tool_calls)
            )
            assistant_message = ChatMessage(
                role="assistant",
                parts=assistant_parts,
                provider_state=resp.provider_state,
            )
            messages.append(assistant_message)
            state.portable_messages.append(assistant_message.portable())

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
                tool_message = ChatMessage(
                    role="tool",
                    parts=(ToolResultPart(tc.id, output),),
                )
                messages.append(tool_message)
                state.portable_messages.append(tool_message)

            if state.budget.exhausted(self.max_tool_rounds):
                messages[:] = build_fresh_synthesis_messages(
                    state.base_request.messages,
                    state.tool_outputs,
                )
                active_tools = ()


def build_provider_router(settings: Settings) -> AIProviderRouter:
    text_order = tuple(settings.text_provider_order_list)
    vision_order = tuple(settings.vision_provider_order_list)
    registered_names = tuple(dict.fromkeys((*text_order, *vision_order)))
    providers = build_provider_targets(settings, registered_names)
    retry_policy = ProviderRetryPolicy(
        max_consecutive_failures=settings.provider_retry_max_consecutive,
        max_failures_per_provider=settings.provider_retry_max_per_provider,
        max_failures_per_request=settings.provider_retry_max_per_request,
        max_recovery_hops_per_request=settings.provider_recovery_max_hops_per_request,
    )
    return AIProviderRouter(
        providers,
        max_tool_rounds=settings.max_tool_rounds,
        text_provider_order=text_order,
        vision_provider_order=vision_order,
        retry_policy=retry_policy,
    )
