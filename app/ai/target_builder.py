"""Generic expansion of declarative provider profiles into concrete targets."""

from __future__ import annotations

from collections.abc import Sequence
from types import MappingProxyType

from ..config import Settings
from .catalog import ProviderCatalog, ProviderProfile, RouteProfile, load_provider_catalog
from .capabilities import ProviderCapabilities
from .drivers.registry import get_driver
from .provider import AIProvider
from .recovery import RecoveryDecision, RecoveryPolicy
from .runtime_config import MAX_AI_PROVIDER_TARGETS, ProviderRuntimeSettings, provider_runtime
from .target import ProviderTargetIdentity, TargetSpec


def _recovery_policy(profile: ProviderProfile) -> RecoveryPolicy:
    overrides = {
        rule.status: RecoveryDecision(rule.action, rule.scope, rule.effect)
        for rule in profile.recovery_rules
    }
    return RecoveryPolicy(overrides=MappingProxyType(overrides))


def build_provider_targets(
    settings: Settings,
    provider_ids: Sequence[str],
    *,
    catalog: ProviderCatalog | None = None,
) -> list[AIProvider]:
    """Build ordered concrete targets without provider-family Python branches."""

    resolved_catalog = catalog or load_provider_catalog()
    names = tuple(
        dict.fromkeys(
            str(raw).strip().lower()
            for raw in provider_ids
            if str(raw).strip()
        )
    )
    profiles = [resolved_catalog.require(name) for name in names]

    text_order = set(settings.text_provider_order_list)
    vision_order = set(settings.vision_provider_order_list)
    prepared: list[
        tuple[
            ProviderProfile,
            ProviderRuntimeSettings,
            tuple[str, ...],
            list[tuple[str, tuple[str, ...], RouteProfile]],
        ]
    ] = []
    target_count = 0

    for profile in profiles:
        runtime = provider_runtime(settings, profile.id)
        credentials = runtime.api_keys_list
        if not credentials:
            continue

        selected_routes: list[tuple[str, tuple[str, ...], RouteProfile]] = []
        route_models = (
            ("text", runtime.text_models_list, profile.id in text_order),
            (
                "vision",
                runtime.vision_models_list,
                settings.vision_enabled and profile.id in vision_order,
            ),
        )
        for route, models, selected in route_models:
            route_profile = profile.routes.get(route)
            if not selected or route_profile is None or not route_profile.enabled:
                continue
            target_count += len(models) * len(credentials)
            if target_count > MAX_AI_PROVIDER_TARGETS:
                raise ValueError(
                    f"AI provider target count exceeds {MAX_AI_PROVIDER_TARGETS}"
                )
            selected_routes.append((route, models, route_profile))
        prepared.append((profile, runtime, credentials, selected_routes))

    targets: list[AIProvider] = []
    for profile, runtime, credentials, selected_routes in prepared:
        driver = get_driver(profile.driver)
        policy = _recovery_policy(profile)
        for route, models, route_profile in selected_routes:
            capabilities = ProviderCapabilities(
                route=route,
                supports_vision=route_profile.supports_vision,
                max_images=route_profile.max_images,
                supports_tools=route_profile.supports_tools,
            )
            for model_index, model in enumerate(models, start=1):
                for credential_index, credential in enumerate(credentials, start=1):
                    credential_id = f"cred-{credential_index}"
                    target_id = (
                        f"{profile.id}:{route}:m{model_index}:c{credential_index}"
                    )
                    spec = TargetSpec(
                        identity=ProviderTargetIdentity(
                            family=profile.id,
                            route=route,
                            model=model,
                            credential_id=credential_id,
                            target_id=target_id,
                        ),
                        driver=profile.driver,
                        capabilities=capabilities,
                        base_url=runtime.resolve_base_url(profile),
                        request_timeout_sec=runtime.resolve_timeout(profile),
                        recovery_policy=policy,
                        driver_options=profile.driver_options,
                    )
                    targets.append(driver.build_target(spec, credential=credential))
    return targets
