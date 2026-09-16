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
    TARGET = "target"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    health_scope: HealthScope


def classify_recovery(
    identity: ProviderTargetIdentity,
    error: ProviderError,
) -> RecoveryDecision:
    """Classify non-transport failures without teaching the router provider quirks."""

    if getattr(error, "transport_kind", None) is not None:
        return RecoveryDecision(RecoveryAction.STOP, HealthScope.TARGET)

    status = error.status_code
    if status == 429:
        scope = (
            HealthScope.CREDENTIAL
            if identity.family == "xkiro"
            else HealthScope.MODEL
        )
        return RecoveryDecision(RecoveryAction.ROTATE_TARGET, scope)
    if status == 401:
        return RecoveryDecision(RecoveryAction.ROTATE_TARGET, HealthScope.CREDENTIAL)
    if status == 402 and identity.family == "xkiro":
        return RecoveryDecision(RecoveryAction.ROTATE_TARGET, HealthScope.CREDENTIAL)
    if status == 404:
        return RecoveryDecision(RecoveryAction.ROTATE_TARGET, HealthScope.MODEL)
    if status == 403 or (status is not None and status >= 500):
        return RecoveryDecision(RecoveryAction.FALLBACK_FAMILY, HealthScope.FAMILY)

    # Preserve the historical behavior for malformed/non-transient provider
    # responses: leave this family and let ordered provider fallback proceed.
    return RecoveryDecision(RecoveryAction.FALLBACK_FAMILY, HealthScope.FAMILY)
