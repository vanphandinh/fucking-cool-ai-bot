"""Stable, secret-free identity and metadata for concrete provider targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

from .capabilities import ProviderCapabilities

if TYPE_CHECKING:
    from .recovery import RecoveryPolicy


@dataclass(frozen=True, slots=True)
class ProviderTargetIdentity:
    """Identify one concrete family/route/model/credential combination."""

    family: str
    route: str
    model: str
    credential_id: str
    target_id: str

    @property
    def family_key(self) -> str:
        return self.family

    @property
    def credential_key(self) -> tuple[str, str]:
        return (self.family, self.credential_id)

    @property
    def model_key(self) -> tuple[str, str]:
        return (self.family, self.model)

    @property
    def entitlement_key(self) -> tuple[str, str, str]:
        return (self.family, self.model, self.credential_id)


@dataclass(frozen=True, slots=True)
class TargetSpec:
    """Explicit immutable metadata for one concrete routing target."""

    identity: ProviderTargetIdentity
    driver: str
    capabilities: ProviderCapabilities
    base_url: str
    request_timeout_sec: float
    recovery_policy: RecoveryPolicy
    driver_options: Mapping[str, object] = field(default_factory=dict)


def provider_target_identity(provider: object) -> ProviderTargetIdentity:
    """Return the explicit secret-free identity attached to a target."""

    return provider.spec.identity  # type: ignore[attr-defined]
