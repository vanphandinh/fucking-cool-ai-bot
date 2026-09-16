"""Scoped provider resource-health regressions."""

from __future__ import annotations

import unittest

from app.ai.recovery import HealthScope
from app.ai.scoped_health import ScopedHealthRegistry
from app.ai.target import ProviderTargetIdentity


def target(
    *,
    route: str = "text",
    model: str = "model-a",
    credential_id: str = "cred-1",
    target_id: str | None = None,
) -> ProviderTargetIdentity:
    return ProviderTargetIdentity(
        family="xkiro",
        route=route,
        model=model,
        credential_id=credential_id,
        target_id=target_id or f"xkiro:{route}:{model}:{credential_id}",
    )


class ScopedProviderHealthTests(unittest.TestCase):
    def test_credential_401_blocks_same_credential_across_routes_only(self) -> None:
        registry = ScopedHealthRegistry()
        text_key1 = target(route="text", credential_id="cred-1")
        vision_key1 = target(route="vision", credential_id="cred-1")
        text_key2 = target(route="text", credential_id="cred-2")

        registry.record_error(
            HealthScope.CREDENTIAL,
            text_key1,
            "auth",
            status_code=401,
            transient=False,
        )

        self.assertFalse(registry.available(text_key1))
        self.assertFalse(registry.available(vision_key1))
        self.assertTrue(registry.available(text_key2))

    def test_model_429_blocks_same_model_but_not_sibling_model(self) -> None:
        registry = ScopedHealthRegistry()
        model_a_key1 = target(model="model-a", credential_id="cred-1")
        model_a_key2 = target(model="model-a", credential_id="cred-2")
        model_b = target(model="model-b", credential_id="cred-1")

        registry.record_error(
            HealthScope.MODEL,
            model_a_key1,
            "quota",
            status_code=429,
            retry_after=120.0,
            transient=True,
        )

        self.assertFalse(registry.available(model_a_key1))
        self.assertFalse(registry.available(model_a_key2))
        self.assertTrue(registry.available(model_b))

    def test_stale_deferred_update_cannot_overwrite_newer_success(self) -> None:
        registry = ScopedHealthRegistry()
        current = target()
        generation = registry.generation(HealthScope.MODEL, current)

        registry.record_success(HealthScope.MODEL, current)
        applied = registry.record_deferred_error(
            HealthScope.MODEL,
            current,
            "stale timeout",
            expected_generation=generation,
            status_code=503,
            transient=True,
        )

        self.assertFalse(applied)
        self.assertTrue(registry.available(current))
        self.assertIsNone(registry.health(HealthScope.MODEL, current).last_error)


if __name__ == "__main__":
    unittest.main()
