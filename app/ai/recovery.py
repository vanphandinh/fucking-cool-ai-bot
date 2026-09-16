"""Provider-specific resource recovery policy for concrete routing targets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .base import ProviderError
from .target import ProviderTargetIdentity


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


def classify_recovery(
    identity: ProviderTargetIdentity,
    error: ProviderError,
) -> RecoveryDecision:
    """Classify non-transport failures without teaching the router provider quirks."""

    if getattr(error, "transport_kind", None) is not None:
        return RecoveryDecision(
            RecoveryAction.STOP,
            HealthScope.TARGET,
            HealthEffect.RECORD_ONLY,
        )

    status = error.status_code
    if status == 429:
        scope = (
            HealthScope.CREDENTIAL
            if identity.family == "xkiro"
            else HealthScope.MODEL
        )
        return RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            scope,
            HealthEffect.COOLDOWN,
        )
    if status == 401:
        return RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            HealthScope.CREDENTIAL,
            HealthEffect.DISABLE,
        )
    if status == 402 and identity.family == "xkiro":
        return RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            HealthScope.CREDENTIAL,
            HealthEffect.DISABLE,
        )
    if status == 403 and identity.family == "xkiro":
        return RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            HealthScope.ENTITLEMENT,
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

    # Preserve the historical behavior for malformed/non-transient provider
    # responses: leave this family and let ordered provider fallback proceed.
    effect = HealthEffect.TRANSIENT if error.transient else HealthEffect.RECORD_ONLY
    return RecoveryDecision(
        RecoveryAction.FALLBACK_FAMILY,
        HealthScope.FAMILY,
        effect,
    )
