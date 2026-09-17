"""Pure recovery-classifier contracts for provider target pools."""

from __future__ import annotations

import unittest

from app.ai.base import ProviderError
from app.ai.recovery import (
    HealthEffect,
    HealthScope,
    RecoveryAction,
    classify_recovery,
)
from app.ai.target import ProviderTargetIdentity


def identity(family: str = "chainnode") -> ProviderTargetIdentity:
    return ProviderTargetIdentity(
        family=family,
        route="text",
        model="Model/Case",
        credential_id="cred-1",
        target_id=f"{family}:text:m1:c1",
    )


class ProviderRecoveryPolicyTests(unittest.TestCase):
    def test_chainnode_429_is_model_scoped_rotation(self) -> None:
        decision = classify_recovery(
            identity("chainnode"),
            ProviderError("quota", status_code=429, transient=True),
        )
        self.assertEqual(decision.action, RecoveryAction.ROTATE_TARGET)
        self.assertEqual(decision.health_scope, HealthScope.MODEL)
        self.assertEqual(decision.health_effect, HealthEffect.COOLDOWN)

    def test_xkiro_429_is_credential_scoped_rotation(self) -> None:
        decision = classify_recovery(
            identity("xkiro"),
            ProviderError("quota", status_code=429, transient=True),
        )
        self.assertEqual(decision.action, RecoveryAction.ROTATE_TARGET)
        self.assertEqual(decision.health_scope, HealthScope.CREDENTIAL)
        self.assertEqual(decision.health_effect, HealthEffect.COOLDOWN)

    def test_auth_and_account_failures_are_credential_scoped(self) -> None:
        for status in (401, 402):
            with self.subTest(status=status):
                decision = classify_recovery(
                    identity("xkiro"),
                    ProviderError("account", status_code=status, transient=False),
                )
                self.assertEqual(decision.action, RecoveryAction.ROTATE_TARGET)
                self.assertEqual(decision.health_scope, HealthScope.CREDENTIAL)
                self.assertEqual(decision.health_effect, HealthEffect.DISABLE)

    def test_chainnode_402_keeps_generic_family_fallback_semantics(self) -> None:
        decision = classify_recovery(
            identity("chainnode"),
            ProviderError("account", status_code=402, transient=False),
        )

        self.assertEqual(decision.action, RecoveryAction.FALLBACK_FAMILY)
        self.assertEqual(decision.health_scope, HealthScope.FAMILY)
        self.assertEqual(decision.health_effect, HealthEffect.RECORD_ONLY)

    def test_xkiro_403_is_entitlement_scoped_disable(self) -> None:
        decision = classify_recovery(
            identity("xkiro"),
            ProviderError("entitlement", status_code=403, transient=False),
        )

        self.assertEqual(decision.action, RecoveryAction.ROTATE_TARGET)
        self.assertEqual(decision.health_scope, HealthScope.ENTITLEMENT)
        self.assertEqual(decision.health_effect, HealthEffect.DISABLE)

    def test_model_not_found_is_model_scoped_disable(self) -> None:
        decision = classify_recovery(
            identity("chainnode"),
            ProviderError("model", status_code=404, transient=False),
        )
        self.assertEqual(decision.action, RecoveryAction.ROTATE_TARGET)
        self.assertEqual(decision.health_scope, HealthScope.MODEL)
        self.assertEqual(decision.health_effect, HealthEffect.DISABLE)

    def test_chainnode_403_and_5xx_fallback_family_without_pool_spray(self) -> None:
        cases = (
            (403, HealthEffect.DISABLE),
            (500, HealthEffect.TRANSIENT),
            (502, HealthEffect.TRANSIENT),
            (503, HealthEffect.TRANSIENT),
        )
        for status, effect in cases:
            with self.subTest(status=status):
                decision = classify_recovery(
                    identity("chainnode"),
                    ProviderError("family", status_code=status, transient=True),
                )
                self.assertEqual(decision.action, RecoveryAction.FALLBACK_FAMILY)
                self.assertEqual(decision.health_scope, HealthScope.FAMILY)
                self.assertEqual(decision.health_effect, effect)


if __name__ == "__main__":
    unittest.main()
