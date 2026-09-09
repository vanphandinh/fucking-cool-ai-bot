"""Regression tests for current B.AI deployment defaults on the generic router."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.router import build_provider_router
from app.config import Settings


class BaiDeploymentDefaultsTests(unittest.TestCase):
    def test_defaults_keep_qwen_orders_and_existing_limits(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.bai_text_model, "qwen3.8-flash")
        self.assertEqual(settings.bai_vision_model, "qwen3.8-flash")
        self.assertEqual(settings.text_provider_order_list, ["bai"])
        self.assertEqual(settings.vision_provider_order_list, ["bai"])
        self.assertEqual(settings.max_images_per_request, 1)
        self.assertEqual(settings.max_context_turns, 6)
        self.assertEqual(settings.max_tool_rounds, 2)

    def test_stale_removed_provider_credentials_are_ignored(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="b",
            gemini_api_key="legacy",
            groq_api_key="legacy",
            openrouter_api_key="legacy",
            cloudflare_account_id="legacy",
            cloudflare_api_token="legacy",
        )
        router = build_provider_router(settings)
        try:
            self.assertEqual(router.configured_provider_names(False), ("bai",))
            self.assertEqual(router.configured_provider_names(True), ("bai",))
            self.assertEqual(router.provider_order(False), ("bai",))
            self.assertEqual(router.provider_order(True), ("bai",))
        finally:
            asyncio.run(_close_router(router))


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
