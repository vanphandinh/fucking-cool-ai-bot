from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

import app.ai.aurora as aurora
from app.config import Settings


class AuroraProviderFactoryTests(unittest.TestCase):
    def test_default_settings_match_private_sidecar(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.aurora_base_url, "http://aurora:8080/v1")
        self.assertEqual(settings.aurora_api_key, "")
        self.assertEqual(settings.aurora_model, "auto")
        self.assertEqual(settings.aurora_request_timeout_sec, 90.0)

    def test_factory_is_text_only_and_uses_temporary_auth_policy(self) -> None:
        settings = SimpleNamespace(
            aurora_base_url="http://aurora:8080/v1",
            aurora_api_key="internal-secret",
            aurora_model="auto",
            aurora_request_timeout_sec=90.0,
        )
        provider = aurora.make_aurora_provider(settings)
        try:
            self.assertEqual(provider.name, "aurora")
            self.assertEqual(provider.model, "auto")
            self.assertEqual(str(provider._client.base_url), "http://aurora:8080/v1/")
            self.assertEqual(provider.capabilities.route, "text")
            self.assertFalse(provider.capabilities.supports_vision)
            self.assertEqual(provider.capabilities.max_images, 0)
            self.assertEqual(provider.health.auth_failure_cooldown_sec, 60.0)
        finally:
            asyncio.run(provider.aclose())


if __name__ == "__main__":
    unittest.main()
