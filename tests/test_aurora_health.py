from __future__ import annotations

import unittest
from unittest.mock import patch

from app.ai.health import ProviderHealth


class AuroraHealthPolicyTests(unittest.TestCase):
    def test_default_auth_failure_still_disables_existing_providers(self) -> None:
        health = ProviderHealth()
        health.record_error("unauthorized", status_code=401, transient=False)
        self.assertTrue(health.disabled)
        self.assertFalse(health.available())

    def test_optional_auth_failure_policy_uses_temporary_cooldown(self) -> None:
        self.assertIn("auth_failure_cooldown_sec", ProviderHealth.__dataclass_fields__)
        health = ProviderHealth(auth_failure_cooldown_sec=60.0)
        with patch("app.ai.health.time.monotonic", return_value=100.0):
            health.record_error("unauthorized", status_code=401, transient=False)
        self.assertFalse(health.disabled)
        self.assertEqual(health.cooldown_until, 160.0)
        self.assertFalse(health.available(now=159.0))
        self.assertTrue(health.available(now=160.0))

    def test_retry_after_overrides_temporary_auth_cooldown(self) -> None:
        self.assertIn("auth_failure_cooldown_sec", ProviderHealth.__dataclass_fields__)
        health = ProviderHealth(auth_failure_cooldown_sec=60.0)
        with patch("app.ai.health.time.monotonic", return_value=200.0):
            health.record_error(
                "forbidden",
                status_code=403,
                retry_after=15.0,
                transient=False,
            )
        self.assertFalse(health.disabled)
        self.assertEqual(health.cooldown_until, 215.0)


if __name__ == "__main__":
    unittest.main()
