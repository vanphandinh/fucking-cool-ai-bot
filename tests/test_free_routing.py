"""Regression tests for OpenCode-primary and optional OpenRouter fallback defaults."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.router import build_provider_router
from app.config import Settings


class ProviderDeploymentDefaultsTests(unittest.TestCase):
    def test_defaults_keep_opencode_primary_and_openrouter_optional_fallback(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(
            settings.text_provider_order_list,
            ["opencode", "openrouter"],
        )
        self.assertEqual(
            settings.vision_provider_order_list,
            ["opencode", "openrouter"],
        )
        self.assertEqual(settings.max_images_per_request, 1)
        self.assertEqual(settings.max_context_turns, 6)
        self.assertEqual(settings.max_tool_rounds, 5)

    def test_no_credentials_builds_no_active_slots_but_preserves_order(self) -> None:
        router = build_provider_router(Settings(_env_file=None))
        try:
            self.assertEqual(router.configured_provider_names(False), ())
            self.assertEqual(router.configured_provider_names(True), ())
            self.assertEqual(
                router.provider_order(False),
                ("opencode", "openrouter"),
            )
            self.assertEqual(
                router.provider_order(True),
                ("opencode", "openrouter"),
            )
        finally:
            asyncio.run(_close_router(router))

    def test_opencode_only_credentials_activate_only_opencode(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                ai_providers={
                    "opencode": {
                        "api_keys": "test-opencode-key",
                        "text_models": "test-opencode-text",
                        "vision_models": "test-opencode-vision",
                    }
                },
            )
        )
        try:
            self.assertEqual(router.configured_provider_names(False), ("opencode",))
            self.assertEqual(router.configured_provider_names(True), ("opencode",))
        finally:
            asyncio.run(_close_router(router))

    def test_both_credentials_activate_opencode_then_openrouter(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                ai_providers={
                    "opencode": {
                        "api_keys": "test-opencode-key",
                        "text_models": "test-opencode-text",
                        "vision_models": "test-opencode-vision",
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
                ("opencode", "openrouter"),
            )
            self.assertEqual(
                router.configured_provider_names(True),
                ("opencode", "openrouter"),
            )
        finally:
            asyncio.run(_close_router(router))


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
