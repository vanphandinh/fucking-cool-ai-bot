"""OpenRouter profile coverage through the generic provider architecture."""

from __future__ import annotations

import asyncio
import json
import unittest

import httpx
from pydantic import ValidationError

from app.ai.catalog import load_provider_catalog
from app.ai.contracts import ChatMessage, ChatRequest, TextPart
from app.ai.recovery import HealthScope
from app.ai.runtime_config import ProviderRuntimeSettings
from app.ai.target_builder import build_provider_targets
from app.config import Settings


class OpenRouterProfileTests(unittest.TestCase):
    def test_recovery_overrides_are_catalog_data(self) -> None:
        rules = {
            rule.status: rule
            for rule in load_provider_catalog().require("openrouter").recovery_rules
        }
        self.assertEqual(rules[429].scope, HealthScope.CREDENTIAL)
        self.assertEqual(rules[403].scope, HealthScope.ENTITLEMENT)

    def test_catalog_declares_provider_preferences_support(self) -> None:
        profile = load_provider_catalog().require("openrouter")
        self.assertIs(profile.driver_options.get("provider_preferences"), True)

    def test_generic_builder_uses_runtime_base_url_and_non_stream_driver(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "openrouter": {
                    "api_keys": "key",
                    "base_url": "https://gateway.example/openrouter/v1",
                    "text_models": "text",
                    "vision_models": "vision",
                }
            },
            text_provider_order="openrouter",
            vision_provider_order="openrouter",
        )
        targets = build_provider_targets(settings, ["openrouter"])
        try:
            self.assertEqual(
                str(targets[0]._client.base_url),
                "https://gateway.example/openrouter/v1/",
            )
            self.assertIs(targets[0].explicit_stream, False)
            self.assertEqual(
                [target.capabilities.route for target in targets],
                ["text", "vision"],
            )
        finally:
            for target in targets:
                asyncio.run(target.aclose())

    def test_provider_slugs_preserve_order_and_drop_duplicates(self) -> None:
        runtime = ProviderRuntimeSettings(
            provider="DeepInfra, together ,DeepInfra,,",
        )
        self.assertEqual(runtime.provider_slugs_list, ["DeepInfra", "together"])

    def test_invalid_provider_slug_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "provider slugs"):
            ProviderRuntimeSettings(provider="deepinfra,bad slug")

    def test_provider_option_is_rejected_without_catalog_opt_in(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "AI_PROVIDERS__CHAINNODE__PROVIDER",
        ):
            Settings(
                _env_file=None,
                ai_providers={
                    "chainnode": {
                        "api_keys": "key",
                        "text_models": "text-model",
                        "provider": "deepinfra",
                    }
                },
                text_provider_order="chainnode",
                vision_enabled=False,
            )

    def test_provider_option_is_rejected_for_unused_non_opted_in_provider(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "AI_PROVIDERS__CHAINNODE__PROVIDER",
        ):
            Settings(
                _env_file=None,
                ai_providers={
                    "chainnode": {
                        "api_keys": "key",
                        "provider": "deepinfra",
                    }
                },
                text_provider_order="",
                vision_provider_order="",
                vision_enabled=False,
            )

    def _text_target(self, *, provider: str):
        settings = Settings(
            _env_file=None,
            ai_providers={
                "openrouter": {
                    "api_keys": "key",
                    "text_models": "text-model",
                    "provider": provider,
                }
            },
            text_provider_order="openrouter",
            vision_enabled=False,
        )
        return build_provider_targets(settings, ["openrouter"])[0]

    def test_provider_preferences_are_sent_with_every_request(self) -> None:
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "ok"}}
                    ]
                },
                request=request,
            )

        target = self._text_target(provider="deepinfra, together, deepinfra")
        asyncio.run(target._client.aclose())
        target._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1/",
            transport=httpx.MockTransport(handler),
        )
        try:
            asyncio.run(
                target.chat(
                    ChatRequest(messages=(ChatMessage("user", (TextPart("hi"),)),))
                )
            )
        finally:
            asyncio.run(target.aclose())

        self.assertEqual(
            captured[0]["provider"],
            {
                "only": ["deepinfra", "together"],
                "order": ["deepinfra", "together"],
            },
        )

    def test_empty_provider_keeps_openrouter_default_routing(self) -> None:
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "ok"}}
                    ]
                },
                request=request,
            )

        target = self._text_target(provider="")
        asyncio.run(target._client.aclose())
        target._client = httpx.AsyncClient(
            base_url="https://openrouter.ai/api/v1/",
            transport=httpx.MockTransport(handler),
        )
        try:
            asyncio.run(
                target.chat(
                    ChatRequest(messages=(ChatMessage("user", (TextPart("hi"),)),))
                )
            )
        finally:
            asyncio.run(target.aclose())

        self.assertNotIn("provider", captured[0])


if __name__ == "__main__":
    unittest.main()
