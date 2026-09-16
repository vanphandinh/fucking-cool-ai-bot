"""Security regressions for provider target identities and diagnostics."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.target import provider_target_identity
from app.ai.xkiro import build_xkiro_provider_slots
from app.config import Settings


class ProviderTargetSecurityTests(unittest.TestCase):
    def test_target_identity_never_contains_raw_api_key(self) -> None:
        secrets = ("SeCrEt-Key-One", "SeCrEt-Key-Two")
        settings = Settings(
            _env_file=None,
            xkiro_api_keys=",".join(secrets),
            xkiro_text_models="Model/Case",
            text_provider_order="xkiro",
            vision_enabled=False,
        )
        slots = build_xkiro_provider_slots(settings)
        try:
            rendered = repr([provider_target_identity(slot) for slot in slots])
            rendered += repr([slot.target_id for slot in slots])
            rendered += repr(slots)
            for secret in secrets:
                self.assertNotIn(secret, rendered)
            self.assertIn("cred-1", rendered)
            self.assertIn("cred-2", rendered)
        finally:
            for slot in slots:
                asyncio.run(slot.aclose())


if __name__ == "__main__":
    unittest.main()
