from __future__ import annotations

import unittest
from unittest.mock import patch

from app.ai.health import ProviderHealth


class ProviderAuthHealthPolicyTests(unittest.TestCase):
    def test_default_401_still_disables_permanently(self) -> None:
        health = ProviderHealth()
        health.record_error("bad key", status_code=401, transient=False)
        self.assertTrue(health.disabled)
        self.assertFalse(health.available())

    def test_temporary_auth_policy_uses_60_second_cooldown(self) -> None:
        health = ProviderHealth(auth_failure_cooldown_sec=60.0)
        with patch("app.ai.health.time.monotonic", return_value=100.0):
            health.record_error("session stale", status_code=401, transient=False)
        self.assertFalse(health.disabled)
        self.assertEqual(health.cooldown_until, 160.0)
        with patch("app.ai.health.time.monotonic", return_value=159.0):
            self.assertFalse(health.available())
        with patch("app.ai.health.time.monotonic", return_value=161.0):
            self.assertTrue(health.available())

    def test_temporary_auth_policy_prefers_retry_after(self) -> None:
        health = ProviderHealth(auth_failure_cooldown_sec=60.0)
        with patch("app.ai.health.time.monotonic", return_value=100.0):
            health.record_error(
                "temporarily blocked",
                status_code=403,
                retry_after=12.0,
                transient=False,
            )
        self.assertFalse(health.disabled)
        self.assertEqual(health.cooldown_until, 112.0)


if __name__ == "__main__":
    unittest.main()
