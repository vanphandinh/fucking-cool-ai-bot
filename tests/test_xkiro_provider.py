"""xKiro profile coverage through the generic provider architecture."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.catalog import load_provider_catalog
from app.ai.recovery import HealthScope
from app.ai.target_builder import build_provider_targets
from app.config import Settings


class XKiroProfileTests(unittest.TestCase):
    def test_recovery_overrides_are_catalog_data(self) -> None:
        rules = {
            rule.status: rule
            for rule in load_provider_catalog().require("xkiro").recovery_rules
        }
        self.assertEqual(rules[429].scope, HealthScope.CREDENTIAL)
        self.assertEqual(rules[403].scope, HealthScope.ENTITLEMENT)

    def test_generic_builder_uses_runtime_base_url_and_non_stream_driver(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "xkiro": {
                    "api_keys": "key",
                    "base_url": "https://gateway.example/xkiro/v1",
                    "text_models": "text",
                    "vision_models": "vision",
                }
            },
            text_provider_order="xkiro",
            vision_provider_order="xkiro",
        )
        targets = build_provider_targets(settings, ["xkiro"])
        try:
            self.assertEqual(
                str(targets[0]._client.base_url),
                "https://gateway.example/xkiro/v1/",
            )
            self.assertIs(targets[0].explicit_stream, False)
            self.assertEqual(
                [target.capabilities.route for target in targets],
                ["text", "vision"],
            )
        finally:
            for target in targets:
                asyncio.run(target.aclose())


if __name__ == "__main__":
    unittest.main()
