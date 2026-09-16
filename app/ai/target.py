"""Stable, secret-free identity for concrete provider routing targets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderTargetIdentity:
    """Identify one concrete family/route/model/credential combination.

    ``credential_id`` and ``target_id`` are opaque aliases only.  Raw API keys
    must never be copied into either field because these identities may appear
    in logs, status output, exceptions, and test diagnostics.
    """

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


def provider_target_identity(provider: object) -> ProviderTargetIdentity:
    """Return a safe identity for an adapter, including legacy single targets."""

    family = str(getattr(provider, "name", "")).strip()
    capabilities = getattr(provider, "capabilities", None)
    route = str(getattr(capabilities, "route", "text") or "text").strip()
    model = str(getattr(provider, "model", "") or "").strip()
    credential_id = str(getattr(provider, "credential_id", "cred-1") or "cred-1").strip()
    explicit_target_id = str(getattr(provider, "target_id", "") or "").strip()
    target_id = explicit_target_id or f"{family}:{route}:{model}:{credential_id}"
    return ProviderTargetIdentity(
        family=family,
        route=route,
        model=model,
        credential_id=credential_id,
        target_id=target_id,
    )
