"""Credential normalization at generic target-builder boundaries."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.target_builder import build_provider_targets
from app.config import Settings


class ProviderApiKeyNormalizationTests(unittest.TestCase):
    def _build(self, provider_id: str, key: str):
        settings = Settings(
            _env_file=None,
            ai_providers={
                provider_id: {
                    "api_keys": key,
                    "text_models": "test-model",
                }
            },
            text_provider_order=provider_id,
            vision_enabled=False,
        )
        return build_provider_targets(settings, [provider_id])

    def test_whitespace_only_key_builds_no_targets(self) -> None:
        for provider_id in ("chainnode", "xkiro"):
            with self.subTest(provider_id=provider_id):
                self.assertEqual(self._build(provider_id, "   "), [])

    def test_builder_strips_credential_before_authorization_header(self) -> None:
        for provider_id in ("chainnode", "xkiro"):
            targets = self._build(provider_id, "  test-key  ")
            try:
                self.assertEqual(
                    targets[0]._client.headers["Authorization"],
                    "Bearer test-key",
                )
            finally:
                for target in targets:
                    asyncio.run(target.aclose())


if __name__ == "__main__":
    unittest.main()
