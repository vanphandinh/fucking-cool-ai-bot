"""Helpers for constructing real protocol targets without vendor modules."""

from __future__ import annotations

from types import MappingProxyType

from app.ai.catalog import load_provider_catalog
from app.ai.capabilities import ProviderCapabilities
from app.ai.drivers.registry import get_driver
from app.ai.recovery import RecoveryDecision, RecoveryPolicy
from app.ai.target import ProviderTargetIdentity, TargetSpec


def make_catalog_target(
    family: str,
    *,
    route: str = "text",
    model: str = "test-model",
    credential: str = "test-key",
    credential_id: str = "cred-1",
    target_id: str | None = None,
):
    profile = load_provider_catalog().require(family)
    route_profile = profile.routes[route]
    overrides = {
        rule.status: RecoveryDecision(rule.action, rule.scope, rule.effect)
        for rule in profile.recovery_rules
    }
    spec = TargetSpec(
        identity=ProviderTargetIdentity(
            family=family,
            route=route,
            model=model,
            credential_id=credential_id,
            target_id=target_id or f"{family}:{route}:m1:c1",
        ),
        driver=profile.driver,
        capabilities=ProviderCapabilities(
            route=route,
            supports_vision=route_profile.supports_vision,
            max_images=route_profile.max_images,
        ),
        base_url=profile.default_base_url,
        request_timeout_sec=30.0,
        recovery_policy=RecoveryPolicy(MappingProxyType(overrides)),
        driver_options=profile.driver_options,
    )
    return get_driver(profile.driver).build_target(spec, credential=credential)
