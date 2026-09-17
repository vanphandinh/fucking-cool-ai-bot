"""Regression tests for Chainnode-primary and xKiro-fallback deployment defaults."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.router import build_provider_router
from app.config import Settings


class ProviderDeploymentDefaultsTests(unittest.TestCase):
    def test_defaults_keep_chainnode_primary_xkiro_fallback_and_existing_limits(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.text_provider_order_list, ["chainnode", "xkiro"])
        self.assertEqual(settings.vision_provider_order_list, ["chainnode", "xkiro"])
        self.assertEqual(settings.max_images_per_request, 1)
        self.assertEqual(settings.max_context_turns, 6)
        self.assertEqual(settings.max_tool_rounds, 2)

    def test_no_credentials_builds_no_active_slots_but_preserves_order(self) -> None:
        router = build_provider_router(Settings(_env_file=None))
        try:
            self.assertEqual(router.configured_provider_names(False), ())
            self.assertEqual(router.configured_provider_names(True), ())
            self.assertEqual(router.provider_order(False), ("chainnode", "xkiro"))
            self.assertEqual(router.provider_order(True), ("chainnode", "xkiro"))
        finally:
            asyncio.run(_close_router(router))

    def test_chainnode_only_credentials_activate_only_chainnode(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                chainnode_api_keys="test-chainnode-key",
                chainnode_text_models="test-chainnode-text",
                chainnode_vision_models="test-chainnode-vision",
            )
        )
        try:
            self.assertEqual(router.configured_provider_names(False), ("chainnode",))
            self.assertEqual(router.configured_provider_names(True), ("chainnode",))
        finally:
            asyncio.run(_close_router(router))

    def test_both_credentials_activate_chainnode_then_xkiro(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                chainnode_api_keys="test-chainnode-key",
                chainnode_text_models="test-chainnode-text",
                chainnode_vision_models="test-chainnode-vision",
                xkiro_api_keys="test-xkiro-key",
                xkiro_text_models="test-xkiro-text",
                xkiro_vision_models="test-xkiro-vision",
            )
        )
        try:
            self.assertEqual(
                router.configured_provider_names(False),
                ("chainnode", "xkiro"),
            )
            self.assertEqual(
                router.configured_provider_names(True),
                ("chainnode", "xkiro"),
            )
        finally:
            asyncio.run(_close_router(router))


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
