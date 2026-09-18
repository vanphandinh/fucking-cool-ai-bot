"""Declarative AI provider catalog loading and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
import tomllib

from .recovery import HealthEffect, HealthScope, RecoveryAction


_CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "ai-providers.toml"
_ALLOWED_ROUTES = frozenset({"text", "vision"})
_PROVIDER_FIELDS = frozenset({
    "id",
    "driver",
    "default_base_url",
    "default_timeout_sec",
    "routes",
    "recovery",
    "driver_options",
})
_ROUTE_FIELDS = frozenset({"enabled", "supports_vision", "max_images", "supports_tools"})
_RECOVERY_FIELDS = frozenset({"status", "action", "scope", "effect"})


@dataclass(frozen=True, slots=True)
class RouteProfile:
    enabled: bool = True
    supports_vision: bool = False
    max_images: int = 0
    supports_tools: bool = True


@dataclass(frozen=True, slots=True)
class RecoveryRule:
    status: int
    action: RecoveryAction
    scope: HealthScope
    effect: HealthEffect


@dataclass(frozen=True, slots=True)
class ProviderProfile:
    id: str
    driver: str
    default_base_url: str
    default_timeout_sec: float
    routes: Mapping[str, RouteProfile]
    recovery_rules: tuple[RecoveryRule, ...] = ()
    driver_options: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderCatalog:
    providers: Mapping[str, ProviderProfile]

    def require(self, provider_id: str) -> ProviderProfile:
        normalized = provider_id.strip().lower()
        try:
            return self.providers[normalized]
        except KeyError as exc:
            raise ValueError(f"unknown AI provider: {provider_id}") from exc


def _enum_value(enum_type: type, value: object, *, label: str):
    try:
        return enum_type(str(value))
    except ValueError as exc:
        raise ValueError(f"unknown recovery {label}: {value}") from exc


def _strict_int(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _reject_unknown_fields(raw: dict, allowed: frozenset[str], *, label: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ValueError(f"{label} has unknown field(s): {', '.join(unknown)}")


def _route_bool(
    provider_id: str,
    route_name: str,
    raw: dict,
    field_name: str,
    default: bool,
) -> bool:
    value = raw.get(field_name, default)
    if not isinstance(value, bool):
        raise ValueError(
            f"provider {provider_id} route {route_name} {field_name} must be boolean"
        )
    return value


def _route_profile(provider_id: str, route_name: str, raw: object) -> RouteProfile:
    if route_name not in _ALLOWED_ROUTES:
        raise ValueError(f"unknown provider route: {route_name}")
    if not isinstance(raw, dict):
        raise ValueError(f"provider {provider_id} route {route_name} must be a table")
    _reject_unknown_fields(
        raw,
        _ROUTE_FIELDS,
        label=f"provider {provider_id} route {route_name}",
    )

    enabled = _route_bool(provider_id, route_name, raw, "enabled", True)
    supports_vision = _route_bool(
        provider_id,
        route_name,
        raw,
        "supports_vision",
        False,
    )
    supports_tools = _route_bool(
        provider_id,
        route_name,
        raw,
        "supports_tools",
        True,
    )
    max_images = _strict_int(
        raw.get("max_images", 0),
        label=f"provider {provider_id} route {route_name} max_images",
    )
    if route_name == "text" and supports_vision:
        raise ValueError(f"provider {provider_id} text route cannot support vision")
    if route_name == "vision" and supports_vision and max_images < 1:
        raise ValueError(f"provider {provider_id} vision max_images must be >= 1")
    if max_images < 0:
        raise ValueError(f"provider {provider_id} route max_images must be >= 0")
    return RouteProfile(
        enabled=enabled,
        supports_vision=supports_vision,
        max_images=max_images,
        supports_tools=supports_tools,
    )


def _recovery_rules(provider_id: str, raw_rules: object) -> tuple[RecoveryRule, ...]:
    if raw_rules is None:
        return ()
    if not isinstance(raw_rules, list):
        raise ValueError(f"provider {provider_id} recovery must be an array")

    seen_statuses: set[int] = set()
    rules: list[RecoveryRule] = []
    for raw in raw_rules:
        if not isinstance(raw, dict):
            raise ValueError(f"provider {provider_id} recovery rule must be a table")
        _reject_unknown_fields(
            raw,
            _RECOVERY_FIELDS,
            label=f"provider {provider_id} recovery rule",
        )
        status = _strict_int(
            raw.get("status", 0),
            label=f"provider {provider_id} recovery status",
        )
        if not 100 <= status <= 599:
            raise ValueError(
                f"provider {provider_id} recovery status must be between 100 and 599"
            )
        if status in seen_statuses:
            raise ValueError(f"duplicate recovery status for provider {provider_id}: {status}")
        seen_statuses.add(status)
        action = _enum_value(RecoveryAction, raw.get("action"), label="action")
        if action is RecoveryAction.STOP:
            raise ValueError("unsupported recovery action in provider catalog: stop")
        rules.append(
            RecoveryRule(
                status=status,
                action=action,
                scope=_enum_value(HealthScope, raw.get("scope"), label="scope"),
                effect=_enum_value(HealthEffect, raw.get("effect"), label="effect"),
            )
        )
    return tuple(rules)


def _provider_profile(raw: object) -> ProviderProfile:
    if not isinstance(raw, dict):
        raise ValueError("provider entry must be a table")
    _reject_unknown_fields(raw, _PROVIDER_FIELDS, label="provider entry")

    provider_id = str(raw.get("id", "")).strip().lower()
    if not provider_id:
        raise ValueError("provider id must be non-empty")
    driver = str(raw.get("driver", "")).strip()
    if not driver:
        raise ValueError(f"provider {provider_id} driver must be non-empty")
    from .drivers.registry import get_driver

    get_driver(driver)
    base_url = str(raw.get("default_base_url", "")).strip()
    raw_timeout = raw.get("default_timeout_sec", 60.0)
    if type(raw_timeout) not in (int, float):
        raise ValueError(f"provider {provider_id} timeout must be a number")
    timeout = float(raw_timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"provider {provider_id} timeout must be finite and > 0")

    raw_routes = raw.get("routes", {})
    if not isinstance(raw_routes, dict):
        raise ValueError(f"provider {provider_id} routes must be a table")
    routes = {
        route_name: _route_profile(provider_id, route_name, route_value)
        for route_name, route_value in raw_routes.items()
    }

    raw_options = raw.get("driver_options", {})
    if not isinstance(raw_options, dict):
        raise ValueError(f"provider {provider_id} driver_options must be a table")

    return ProviderProfile(
        id=provider_id,
        driver=driver,
        default_base_url=base_url,
        default_timeout_sec=timeout,
        routes=MappingProxyType(routes),
        recovery_rules=_recovery_rules(provider_id, raw.get("recovery")),
        driver_options=MappingProxyType(dict(raw_options)),
    )


def load_provider_catalog(path: Path | None = None) -> ProviderCatalog:
    """Load provider profiles from TOML and validate the declarative schema."""

    catalog_path = path or _CATALOG_PATH
    with catalog_path.open("rb") as handle:
        raw = tomllib.load(handle)

    raw_providers = raw.get("providers", [])
    if not isinstance(raw_providers, list):
        raise ValueError("providers must be an array")

    providers: dict[str, ProviderProfile] = {}
    for raw_provider in raw_providers:
        profile = _provider_profile(raw_provider)
        if profile.id in providers:
            raise ValueError(f"duplicate provider id: {profile.id}")
        providers[profile.id] = profile
    return ProviderCatalog(providers=MappingProxyType(providers))
