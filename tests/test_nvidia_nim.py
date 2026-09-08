"""Regression tests for NVIDIA NIM provider integration."""

from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from app.ai.base import OpenAICompatProvider, ProviderError
from app.ai.router import build_provider_router
from app.config import Settings


class NvidiaSettingsTests(unittest.TestCase):
    def test_defaults_make_nvidia_primary_when_a_key_is_configured(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.nvidia_nim_api_key, "")
        self.assertEqual(settings.nvidia_nim_base_url, "https://integrate.api.nvidia.com/v1")
        self.assertEqual(settings.nvidia_nim_text_model, "deepseek-ai/deepseek-v4-flash-0731")
        self.assertEqual(settings.nvidia_nim_vision_model, "google/gemma-4-31b-it")
        self.assertEqual(
            settings.text_provider_order,
            "nvidia,groq,cloudflare,openrouter,gemini",
        )
        self.assertNotIn("nvidia", settings.vision_provider_order_list)

    def test_configured_provider_names_include_nvidia_in_effective_order(self) -> None:
        settings = Settings(
            _env_file=None,
            nvidia_nim_api_key="nvapi-test",
            groq_api_key="groq-test",
            text_provider_order="nvidia,groq,nvidia",
        )

        self.assertEqual(settings.configured_provider_names, ["nvidia", "groq"])
        self.assertEqual(settings.text_provider_order_list, ["nvidia", "groq"])


class NvidiaRouterTests(unittest.TestCase):
    def test_router_places_nvidia_before_existing_text_fallbacks(self) -> None:
        settings = Settings(
            _env_file=None,
            nvidia_nim_api_key="nvapi-test",
            groq_api_key="groq-test",
            cloudflare_account_id="account",
            cloudflare_api_token="token",
            openrouter_api_key="openrouter-test",
            gemini_api_key="gemini-test",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        try:
            text = router.capable_providers(requires_vision=False)
            self.assertEqual(
                [provider.name for provider in text],
                ["nvidia", "groq", "cloudflare_text", "openrouter", "gemini"],
            )
            self.assertEqual(text[0].model, "deepseek-ai/deepseek-v4-flash-0731")
        finally:
            asyncio.run(_close_router(router))

    def test_missing_nvidia_key_keeps_existing_fallback_pool_usable(self) -> None:
        settings = Settings(
            _env_file=None,
            groq_api_key="groq-test",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        try:
            text = router.capable_providers(requires_vision=False)
            self.assertEqual([provider.name for provider in text], ["groq"])
        finally:
            asyncio.run(_close_router(router))

    def test_nvidia_vision_is_opt_in_via_provider_order(self) -> None:
        settings = Settings(
            _env_file=None,
            nvidia_nim_api_key="nvapi-test",
            vision_provider_order="nvidia,gemini",
            gemini_api_key="gemini-test",
        )
        router = build_provider_router(settings)
        try:
            vision = router.capable_providers(requires_vision=True, image_count=1)
            self.assertEqual(
                [provider.name for provider in vision],
                ["nvidia_vision", "gemini_vision"],
            )
            self.assertEqual(vision[0].model, "google/gemma-4-31b-it")
        finally:
            asyncio.run(_close_router(router))


class NvidiaRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_nvidia_explicitly_disables_streaming(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                request=request,
            )

        settings = Settings(
            _env_file=None,
            nvidia_nim_api_key="nvapi-test",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        provider = router.capable_providers(requires_vision=False)[0]
        await provider._client.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.invalid/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            response = await provider.chat([{"role": "user", "content": "hello"}])
        finally:
            await _close_router(router)

        self.assertEqual(response.content, "ok")
        self.assertEqual(requests[0]["stream"], False)


class PendingResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_202_is_classified_as_transient_pending_response(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                202,
                json={"requestId": "pending-123"},
                request=request,
            )

        provider = OpenAICompatProvider(
            "nvidia",
            "https://example.invalid/v1",
            "fake",
            "deepseek-ai/deepseek-v4-flash-0731",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.invalid/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            with self.assertRaises(ProviderError) as ctx:
                await provider.chat([{"role": "user", "content": "hello"}])
        finally:
            await provider.aclose()

        self.assertEqual(ctx.exception.status_code, 202)
        self.assertTrue(ctx.exception.transient)
        self.assertIn("pending", str(ctx.exception).lower())


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
