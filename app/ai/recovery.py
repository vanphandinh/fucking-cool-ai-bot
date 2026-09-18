"""Provider-agnostic resource recovery policy for concrete routing targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .base import ProviderError
from .target import TargetSpec


class RecoveryAction(str, Enum):
    ROTATE_TARGET = "rotate_target"
    FALLBACK_FAMILY = "fallback_family"
    STOP = "stop"


class HealthScope(str, Enum):
    FAMILY = "family"
    CREDENTIAL = "credential"
    MODEL = "model"
    ENTITLEMENT = "entitlement"
    TARGET = "target"


class HealthEffect(str, Enum):
    DISABLE = "disable"
    COOLDOWN = "cooldown"
    TRANSIENT = "transient"
    RECORD_ONLY = "record_only"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    health_scope: HealthScope
    health_effect: HealthEffect


TRANSPORT_RECORD_ONLY = RecoveryDecision(
    RecoveryAction.STOP,
    HealthScope.TARGET,
    HealthEffect.RECORD_ONLY,
)


def default_recovery_decision(error: ProviderError) -> RecoveryDecision:
    status = error.status_code
    if status == 429:
        return RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            HealthScope.MODEL,
            HealthEffect.COOLDOWN,
        )
    if status == 401:
        return RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            HealthScope.CREDENTIAL,
            HealthEffect.DISABLE,
        )
    if status == 404:
        return RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            HealthScope.MODEL,
            HealthEffect.DISABLE,
        )
    if status == 403:
        return RecoveryDecision(
            RecoveryAction.FALLBACK_FAMILY,
            HealthScope.FAMILY,
            HealthEffect.DISABLE,
        )
    if status is not None and status >= 500:
        return RecoveryDecision(
            RecoveryAction.FALLBACK_FAMILY,
            HealthScope.FAMILY,
            HealthEffect.TRANSIENT,
        )

    effect = HealthEffect.TRANSIENT if error.transient else HealthEffect.RECORD_ONLY
    return RecoveryDecision(
        RecoveryAction.FALLBACK_FAMILY,
        HealthScope.FAMILY,
        effect,
    )


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    overrides: Mapping[int, RecoveryDecision] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def decision_for(self, error: ProviderError) -> RecoveryDecision:
        if getattr(error, "transport_kind", None) is not None:
            return TRANSPORT_RECORD_ONLY
        if error.status_code is not None:
            override = self.overrides.get(error.status_code)
            if override is not None:
                return override
        return default_recovery_decision(error)


def classify_recovery(spec: TargetSpec, error: ProviderError) -> RecoveryDecision:
    """Classify a failure using only the target's declarative recovery policy."""

    return spec.recovery_policy.decision_for(error)
