"""Pure recovery-classifier contracts for declarative target policies."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
import unittest

from app.ai.base import ProviderError
from app.ai.capabilities import ProviderCapabilities
from app.ai.recovery import (
    HealthEffect,
    HealthScope,
    RecoveryAction,
    RecoveryDecision,
    RecoveryPolicy,
    classify_recovery,
)
from app.ai.target import ProviderTargetIdentity, TargetSpec


def spec(*, overrides: dict[int, RecoveryDecision] | None = None) -> TargetSpec:
    return TargetSpec(
        identity=ProviderTargetIdentity(
            family="demo",
            route="text",
            model="Model/Case",
            credential_id="cred-1",
            target_id="demo:text:m1:c1",
        ),
        driver="fake",
        capabilities=ProviderCapabilities(route="text"),
        base_url="https://example.invalid/v1",
        request_timeout_sec=60.0,
        recovery_policy=RecoveryPolicy(
            MappingProxyType(dict(overrides or {}))
        ),
    )


class ProviderRecoveryPolicyTests(unittest.TestCase):
    def assert_decision(self, status: int, expected, *, overrides=None, transient=False):
        decision = classify_recovery(
            spec(overrides=overrides),
            ProviderError("failure", status_code=status, transient=transient),
        )
        self.assertEqual(
            (decision.action, decision.health_scope, decision.health_effect),
            expected,
        )

    def test_default_429_is_model_cooldown_rotation(self) -> None:
        self.assert_decision(
            429,
            (RecoveryAction.ROTATE_TARGET, HealthScope.MODEL, HealthEffect.COOLDOWN),
        )

    def test_override_429_can_be_credential_cooldown_rotation(self) -> None:
        override = RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            HealthScope.CREDENTIAL,
            HealthEffect.COOLDOWN,
        )
        self.assert_decision(
            429,
            (RecoveryAction.ROTATE_TARGET, HealthScope.CREDENTIAL, HealthEffect.COOLDOWN),
            overrides={429: override},
        )

    def test_default_401_is_credential_disable_rotation(self) -> None:
        self.assert_decision(
            401,
            (RecoveryAction.ROTATE_TARGET, HealthScope.CREDENTIAL, HealthEffect.DISABLE),
        )

    def test_exact_402_override_is_credential_disable_rotation(self) -> None:
        override = RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            HealthScope.CREDENTIAL,
            HealthEffect.DISABLE,
        )
        self.assert_decision(
            402,
            (RecoveryAction.ROTATE_TARGET, HealthScope.CREDENTIAL, HealthEffect.DISABLE),
            overrides={402: override},
        )

    def test_exact_403_override_can_be_entitlement_disable_rotation(self) -> None:
        override = RecoveryDecision(
            RecoveryAction.ROTATE_TARGET,
            HealthScope.ENTITLEMENT,
            HealthEffect.DISABLE,
        )
        self.assert_decision(
            403,
            (RecoveryAction.ROTATE_TARGET, HealthScope.ENTITLEMENT, HealthEffect.DISABLE),
            overrides={403: override},
        )

    def test_default_403_is_family_disable_fallback(self) -> None:
        self.assert_decision(
            403,
            (RecoveryAction.FALLBACK_FAMILY, HealthScope.FAMILY, HealthEffect.DISABLE),
        )

    def test_default_404_is_model_disable_rotation(self) -> None:
        self.assert_decision(
            404,
            (RecoveryAction.ROTATE_TARGET, HealthScope.MODEL, HealthEffect.DISABLE),
        )

    def test_5xx_is_family_transient_fallback(self) -> None:
        self.assert_decision(
            503,
            (RecoveryAction.FALLBACK_FAMILY, HealthScope.FAMILY, HealthEffect.TRANSIENT),
            transient=True,
        )

    def test_classifier_source_contains_no_provider_family_names(self) -> None:
        source = (
            Path(__file__).parents[1] / "app" / "ai" / "recovery.py"
        ).read_text(encoding="utf-8").lower()
        self.assertNotIn("chainnode", source)
        self.assertNotIn("xkiro", source)


if __name__ == "__main__":
    unittest.main()
