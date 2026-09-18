"""Chainnode profile coverage through the generic provider architecture."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.catalog import load_provider_catalog
from app.ai.target_builder import build_provider_targets
from app.config import Settings

TEXT_MODEL = "cl/cline-free/deepseek-v4.1-flash"
VISION_MODEL = "cl/cline-free/muse-spark-1.3-contributor"


class ChainnodeProfileTests(unittest.TestCase):
    def test_catalog_defaults_and_routes(self) -> None:
        profile = load_provider_catalog().require("chainnode")
        self.assertEqual(profile.driver, "openai-chat")
        self.assertEqual(profile.default_base_url, "https://dn.chainno.de/v1")
        self.assertTrue(profile.routes["vision"].supports_vision)
        self.assertEqual(profile.routes["vision"].max_images, 1)

    def test_generic_builder_uses_models_and_timeout(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "chainnode": {
                    "api_keys": "key",
                    "text_models": TEXT_MODEL,
                    "vision_models": VISION_MODEL,
                    "request_timeout_sec": 25,
                }
            },
            text_provider_order="chainnode",
            vision_provider_order="chainnode",
        )
        targets = build_provider_targets(settings, ["chainnode"])
        try:
            self.assertEqual([target.model for target in targets], [TEXT_MODEL, VISION_MODEL])
            self.assertEqual(
                [target.capabilities.route for target in targets],
                ["text", "vision"],
            )
            self.assertEqual(targets[0]._client.timeout.read, 25.0)
        finally:
            for target in targets:
                asyncio.run(target.aclose())

    def test_vision_route_can_be_selected_without_text(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "chainnode": {
                    "api_keys": "key",
                    "vision_models": VISION_MODEL,
                }
            },
            text_provider_order="xkiro",
            vision_provider_order="chainnode",
        )
        targets = build_provider_targets(settings, ["chainnode"])
        try:
            self.assertEqual(
                [target.capabilities.route for target in targets],
                ["vision"],
            )
        finally:
            for target in targets:
                asyncio.run(target.aclose())


if __name__ == "__main__":
    unittest.main()
