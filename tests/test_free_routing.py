"""Regression tests for free-first AI provider routing."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.router import build_provider_router
from app.config import Settings


class FreeFirstDefaultsTests(unittest.TestCase):
    def test_defaults_prefer_free_tier_models_and_conserve_quota(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.groq_model, "openai/gpt-oss-120b")
        self.assertEqual(settings.cloudflare_text_model, "@cf/zai-org/glm-4.7-flash")
        self.assertEqual(settings.openrouter_model, "openrouter/free")
        self.assertEqual(settings.gemini_model, "gemini-3.8-flash")
        self.assertEqual(settings.text_provider_order, "groq,cloudflare,openrouter,gemini")
        self.assertEqual(
            settings.vision_provider_order,
            "groq_qwen38,cloudflare,groq_qwen36,gemini",
        )
        self.assertEqual(settings.max_context_turns, 6)
        self.assertEqual(settings.max_tool_rounds, 2)

    def test_reported_text_providers_match_effective_order(self) -> None:
        settings = Settings(
            _env_file=None,
            gemini_api_key="g",
            groq_api_key="q",
            openrouter_api_key="o",
            cloudflare_account_id="account",
            cloudflare_api_token="token",
            text_provider_order="cloudflare,groq,gemini,openrouter",
        )

        self.assertEqual(
            settings.configured_provider_names,
            ["cloudflare", "groq", "gemini", "openrouter"],
        )

        missing_cloudflare_credentials = Settings(
            _env_file=None,
            groq_api_key="q",
            text_provider_order="cloudflare,groq",
        )
        self.assertEqual(missing_cloudflare_credentials.configured_provider_names, ["groq"])


class FreeFirstRouterTests(unittest.TestCase):
    def test_text_router_uses_configured_free_first_order_and_models(self) -> None:
        settings = Settings(
            _env_file=None,
            gemini_api_key="g",
            groq_api_key="q",
            openrouter_api_key="o",
            cloudflare_account_id="account",
            cloudflare_api_token="token",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        try:
            text = router.capable_providers(requires_vision=False)
            self.assertEqual([provider.name for provider in text], [
                "groq",
                "cloudflare_text",
                "openrouter",
                "gemini",
            ])
            self.assertEqual([provider.model for provider in text], [
                "openai/gpt-oss-120b",
                "@cf/zai-org/glm-4.7-flash",
                "openrouter/free",
                "gemini-3.8-flash",
            ])
        finally:
            asyncio.run(_close_router(router))

    def test_vision_router_prefers_provider_diversity_before_second_groq_slot(self) -> None:
        settings = Settings(
            _env_file=None,
            gemini_api_key="g",
            groq_api_key="q",
            cloudflare_account_id="account",
            cloudflare_api_token="token",
        )
        router = build_provider_router(settings)
        try:
            vision = router.capable_providers(requires_vision=True, image_count=1)
            self.assertEqual([provider.name for provider in vision], [
                "groq_qwen38",
                "cloudflare",
                "groq_qwen36",
                "gemini_vision",
            ])
        finally:
            asyncio.run(_close_router(router))


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
