"""Pure recovery-classifier contracts for provider target pools."""

from __future__ import annotations

import unittest

from app.ai.base import ProviderError
from app.ai.recovery import HealthScope, RecoveryAction, classify_recovery
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

    def test_xkiro_429_is_credential_scoped_rotation(self) -> None:
        decision = classify_recovery(
            identity("xkiro"),
            ProviderError("quota", status_code=429, transient=True),
        )
        self.assertEqual(decision.action, RecoveryAction.ROTATE_TARGET)
        self.assertEqual(decision.health_scope, HealthScope.CREDENTIAL)

    def test_auth_and_account_failures_are_credential_scoped(self) -> None:
        for status in (401, 402):
            with self.subTest(status=status):
                decision = classify_recovery(
                    identity("xkiro"),
                    ProviderError("account", status_code=status, transient=False),
                )
                self.assertEqual(decision.action, RecoveryAction.ROTATE_TARGET)
                self.assertEqual(decision.health_scope, HealthScope.CREDENTIAL)

    def test_model_not_found_is_model_scoped(self) -> None:
        decision = classify_recovery(
            identity("chainnode"),
            ProviderError("model", status_code=404, transient=False),
        )
        self.assertEqual(decision.action, RecoveryAction.ROTATE_TARGET)
        self.assertEqual(decision.health_scope, HealthScope.MODEL)

    def test_403_and_5xx_fallback_family_without_pool_spray(self) -> None:
        for status in (403, 500, 502, 503):
            with self.subTest(status=status):
                decision = classify_recovery(
                    identity("chainnode"),
                    ProviderError("family", status_code=status, transient=True),
                )
                self.assertEqual(decision.action, RecoveryAction.FALLBACK_FAMILY)
                self.assertEqual(decision.health_scope, HealthScope.FAMILY)


if __name__ == "__main__":
    unittest.main()
