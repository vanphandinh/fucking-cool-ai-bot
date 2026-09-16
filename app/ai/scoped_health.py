"""Health registry for provider family, credential, model, entitlement, and target scopes."""

from __future__ import annotations

from .health import ProviderHealth
from .recovery import HealthEffect, HealthScope
from .target import ProviderTargetIdentity


class ScopedHealthRegistry:
    def __init__(self) -> None:
        self._families: dict[str, ProviderHealth] = {}
        self._credentials: dict[tuple[str, str], ProviderHealth] = {}
        self._models: dict[tuple[str, str], ProviderHealth] = {}
        self._entitlements: dict[tuple[str, str, str], ProviderHealth] = {}
        self._targets: dict[str, ProviderHealth] = {}

    def _store(self, scope: HealthScope):
        if scope == HealthScope.FAMILY:
            return self._families
        if scope == HealthScope.CREDENTIAL:
            return self._credentials
        if scope == HealthScope.MODEL:
            return self._models
        if scope == HealthScope.ENTITLEMENT:
            return self._entitlements
        return self._targets

    @staticmethod
    def _key(scope: HealthScope, identity: ProviderTargetIdentity):
        if scope == HealthScope.FAMILY:
            return identity.family_key
        if scope == HealthScope.CREDENTIAL:
            return identity.credential_key
        if scope == HealthScope.MODEL:
            return identity.model_key
        if scope == HealthScope.ENTITLEMENT:
            return identity.entitlement_key
        return identity.target_id

    def health(
        self,
        scope: HealthScope,
        identity: ProviderTargetIdentity,
    ) -> ProviderHealth:
        store = self._store(scope)
        key = self._key(scope, identity)
        return store.setdefault(key, ProviderHealth())

    def generation(
        self,
        scope: HealthScope,
        identity: ProviderTargetIdentity,
    ) -> int:
        return self.health(scope, identity).generation

    def available(
        self,
        identity: ProviderTargetIdentity,
        *,
        now: float | None = None,
    ) -> bool:
        return all(
            self.health(scope, identity).available(now)
            for scope in (
                HealthScope.FAMILY,
                HealthScope.CREDENTIAL,
                HealthScope.MODEL,
                HealthScope.ENTITLEMENT,
                HealthScope.TARGET,
            )
        )

    def record_success(
        self,
        scope: HealthScope,
        identity: ProviderTargetIdentity,
    ) -> None:
        self.health(scope, identity).record_success()

    def record_success_if_generation(
        self,
        scope: HealthScope,
        identity: ProviderTargetIdentity,
        *,
        expected_generation: int,
    ) -> bool:
        return self.health(scope, identity).record_success_if_generation(
            expected_generation
        )

    def record_effect(
        self,
        scope: HealthScope,
        identity: ProviderTargetIdentity,
        message: str,
        *,
        effect: HealthEffect,
        retry_after: float | None = None,
    ) -> None:
        health = self.health(scope, identity)
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

    def record_error(
        self,
        scope: HealthScope,
        identity: ProviderTargetIdentity,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        transient: bool = True,
    ) -> None:
        self.health(scope, identity).record_error(
            message,
            status_code=status_code,
            retry_after=retry_after,
            transient=transient,
        )

    def record_deferred_error(
        self,
        scope: HealthScope,
        identity: ProviderTargetIdentity,
        message: str,
        *,
        expected_generation: int,
        status_code: int | None = None,
        retry_after: float | None = None,
        transient: bool = True,
    ) -> bool:
        return self.health(scope, identity).record_deferred_error(
            message,
            expected_generation=expected_generation,
            status_code=status_code,
            retry_after=retry_after,
            transient=transient,
        )
