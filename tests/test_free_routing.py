"""Regression tests for Chainnode-primary and OpenRouter-fallback deployment defaults."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.router import build_provider_router
from app.config import Settings


class ProviderDeploymentDefaultsTests(unittest.TestCase):
    def test_defaults_keep_chainnode_primary_openrouter_fallback_and_updated_limits(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.text_provider_order_list, ["chainnode", "openrouter"])
        self.assertEqual(settings.vision_provider_order_list, ["chainnode", "openrouter"])
        self.assertEqual(settings.max_images_per_request, 1)
        self.assertEqual(settings.max_context_turns, 6)
        self.assertEqual(settings.max_tool_rounds, 5)

    def test_no_credentials_builds_no_active_slots_but_preserves_order(self) -> None:
        router = build_provider_router(Settings(_env_file=None))
        try:
            self.assertEqual(router.configured_provider_names(False), ())
            self.assertEqual(router.configured_provider_names(True), ())
            self.assertEqual(router.provider_order(False), ("chainnode", "openrouter"))
            self.assertEqual(router.provider_order(True), ("chainnode", "openrouter"))
        finally:
            asyncio.run(_close_router(router))

    def test_chainnode_only_credentials_activate_only_chainnode(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                ai_providers={
                    "chainnode": {
                        "api_keys": "test-chainnode-key",
                        "text_models": "test-chainnode-text",
                        "vision_models": "test-chainnode-vision",
                    }
                },
            )
        )
        try:
            self.assertEqual(router.configured_provider_names(False), ("chainnode",))
            self.assertEqual(router.configured_provider_names(True), ("chainnode",))
        finally:
            asyncio.run(_close_router(router))

    def test_both_credentials_activate_chainnode_then_openrouter(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                ai_providers={
                    "chainnode": {
                        "api_keys": "test-chainnode-key",
                        "text_models": "test-chainnode-text",
                        "vision_models": "test-chainnode-vision",
                    },
                    "openrouter": {
                        "api_keys": "test-openrouter-key",
                        "text_models": "test-openrouter-text",
                        "vision_models": "test-openrouter-vision",
                    },
                },
            )
        )
        try:
            self.assertEqual(
                router.configured_provider_names(False),
                ("chainnode", "openrouter"),
            )
            self.assertEqual(
                router.configured_provider_names(True),
                ("chainnode", "openrouter"),
            )
        finally:
            asyncio.run(_close_router(router))


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
