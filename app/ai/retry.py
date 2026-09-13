"""Request-scoped bounded retry state for transient AI transport failures."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .base import ProviderError, TransportFailureKind


@dataclass(frozen=True)
class ProviderRetryPolicy:
    max_consecutive_failures: int = 2
    max_failures_per_provider: int = 3
    max_failures_per_request: int = 5


@dataclass
class ProviderAttemptState:
    consecutive_transport_failures: int = 0
    total_transport_failures: int = 0
    pending_health_error: ProviderError | None = None
    blocked_for_request: bool = False
    same_provider_retry_consumed: bool = False


class TransportRetryAction(str, Enum):
    RETRY_SAME = "retry_same"
    ROTATE = "rotate"
    STOP = "stop"


@dataclass
class RequestRetryState:
    policy: ProviderRetryPolicy
    providers: dict[str, ProviderAttemptState] = field(default_factory=dict)
    total_transport_failures: int = 0

    def provider_state(self, provider_name: str) -> ProviderAttemptState:
        return self.providers.setdefault(provider_name, ProviderAttemptState())

    def record_transport_failure(self, provider_name: str, error: ProviderError) -> None:
        slot = self.provider_state(provider_name)
        slot.consecutive_transport_failures += 1
        slot.total_transport_failures += 1
        slot.pending_health_error = error
        self.total_transport_failures += 1
        if (
            slot.consecutive_transport_failures >= self.policy.max_consecutive_failures
            or slot.total_transport_failures >= self.policy.max_failures_per_provider
        ):
            slot.blocked_for_request = True

    def record_chat_success(self, provider_name: str) -> None:
        slot = self.provider_state(provider_name)
        slot.consecutive_transport_failures = 0
        slot.pending_health_error = None

    def block_provider(self, provider_name: str) -> None:
        self.provider_state(provider_name).blocked_for_request = True

    def request_transport_budget_exhausted(self) -> bool:
        return self.total_transport_failures >= self.policy.max_failures_per_request

    def can_attempt(self, provider_name: str) -> bool:
        if self.request_transport_budget_exhausted():
            return False
        slot = self.provider_state(provider_name)
        return (
            not slot.blocked_for_request
            and slot.total_transport_failures < self.policy.max_failures_per_provider
            and slot.consecutive_transport_failures < self.policy.max_consecutive_failures
        )

    def can_retry_same(self, provider_name: str) -> bool:
        slot = self.provider_state(provider_name)
        return self.can_attempt(provider_name) and not slot.same_provider_retry_consumed

    def consume_same_provider_retry(self, provider_name: str) -> bool:
        if not self.can_retry_same(provider_name):
            return False
        self.provider_state(provider_name).same_provider_retry_consumed = True
        return True


def transport_kind(error: ProviderError) -> TransportFailureKind | None:
    kind = getattr(error, "transport_kind", None)
    if isinstance(kind, TransportFailureKind):
        return kind
    if isinstance(kind, str):
        try:
            return TransportFailureKind(kind)
        except ValueError:
            return None
    return None


def is_cyclic_retryable_transport(error: ProviderError) -> bool:
    return transport_kind(error) in {
        TransportFailureKind.CONNECT_ERROR,
        TransportFailureKind.CONNECT_TIMEOUT,
        TransportFailureKind.READ_TIMEOUT,
    }
