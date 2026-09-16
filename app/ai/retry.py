"""Request-scoped bounded retry and resource-recovery state."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .base import ProviderError, TransportFailureKind


@dataclass(frozen=True)
class ProviderRetryPolicy:
    max_consecutive_failures: int = 2
    max_failures_per_provider: int = 3
    max_failures_per_request: int = 5
    max_recovery_hops_per_request: int = 5


@dataclass
class DeferredHealthIncident:
    target_id: str
    error: ProviderError
    health_barrier_generation: int | None


@dataclass
class ProviderAttemptState:
    """Transport state shared by every target in one provider family."""

    consecutive_transport_failures: int = 0
    total_transport_failures: int = 0
    pending_health_incidents: dict[str, DeferredHealthIncident] = field(
        default_factory=dict
    )
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
    blocked_targets: set[str] = field(default_factory=set)
    total_transport_failures: int = 0
    total_recovery_hops: int = 0

    def family_state(self, family: str) -> ProviderAttemptState:
        return self.providers.setdefault(family, ProviderAttemptState())

    # Backward-compatible name retained for existing retry-state tests/callers.
    def provider_state(self, provider_name: str) -> ProviderAttemptState:
        return self.family_state(provider_name)

    def record_transport_failure(
        self,
        family: str,
        error: ProviderError,
        *,
        health_barrier_generation: int | None = None,
        target_id: str | None = None,
    ) -> None:
        slot = self.family_state(family)
        slot.consecutive_transport_failures += 1
        slot.total_transport_failures += 1
        if target_id is not None:
            incident = slot.pending_health_incidents.get(target_id)
            if incident is None:
                slot.pending_health_incidents[target_id] = DeferredHealthIncident(
                    target_id=target_id,
                    error=error,
                    health_barrier_generation=health_barrier_generation,
                )
            else:
                incident.error = error
        self.total_transport_failures += 1
        if (
            slot.consecutive_transport_failures >= self.policy.max_consecutive_failures
            or slot.total_transport_failures >= self.policy.max_failures_per_provider
        ):
            slot.blocked_for_request = True

    def discard_pending_health_incident(self, family: str, target_id: str) -> None:
        self.family_state(family).pending_health_incidents.pop(target_id, None)

    def pending_health_incidents(self, family: str) -> tuple[DeferredHealthIncident, ...]:
        return tuple(self.family_state(family).pending_health_incidents.values())

    def record_chat_success(self, family: str, *, target_id: str | None = None) -> None:
        slot = self.family_state(family)
        slot.consecutive_transport_failures = 0
        if target_id is None:
            slot.pending_health_incidents.clear()
        else:
            slot.pending_health_incidents.pop(target_id, None)

    def block_family(self, family: str) -> None:
        self.family_state(family).blocked_for_request = True

    # Compatibility alias: the historical provider key was the family name.
    def block_provider(self, provider_name: str) -> None:
        self.block_family(provider_name)

    def block_target(self, target_id: str) -> None:
        self.blocked_targets.add(target_id)

    def request_transport_budget_exhausted(self) -> bool:
        return self.total_transport_failures >= self.policy.max_failures_per_request

    def request_recovery_budget_exhausted(self) -> bool:
        return self.total_recovery_hops >= self.policy.max_recovery_hops_per_request

    def consume_recovery_hop(self) -> bool:
        if self.request_recovery_budget_exhausted():
            return False
        self.total_recovery_hops += 1
        return True

    def can_attempt(self, family: str) -> bool:
        if self.request_transport_budget_exhausted():
            return False
        slot = self.family_state(family)
        return (
            not slot.blocked_for_request
            and slot.total_transport_failures < self.policy.max_failures_per_provider
            and slot.consecutive_transport_failures < self.policy.max_consecutive_failures
        )

    def can_attempt_target(self, family: str, target_id: str) -> bool:
        return target_id not in self.blocked_targets and self.can_attempt(family)

    def can_retry_same(self, family: str) -> bool:
        slot = self.family_state(family)
        return self.can_attempt(family) and not slot.same_provider_retry_consumed

    def consume_same_provider_retry(self, family: str) -> bool:
        if not self.can_retry_same(family):
            return False
        self.family_state(family).same_provider_retry_consumed = True
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
