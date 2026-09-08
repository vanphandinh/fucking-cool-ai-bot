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
        self.assertEqual(
            settings.nvidia_nim_text_model,
            "nvidia/nemotron-3.5-lightning-30b-a3b",
        )
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
            self.assertEqual(text[0].model, "nvidia/nemotron-3.5-lightning-30b-a3b")
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

    async def test_nvidia_tool_request_keeps_non_streaming_contract(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "call_search",
                                            "type": "function",
                                            "function": {
                                                "name": "web_search",
                                                "arguments": '{"query":"nvidia nim"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
                request=request,
            )

        settings = Settings(
            _env_file=None,
            nvidia_nim_api_key="nvapi-test",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        nvidia = router.capable_providers(requires_vision=False)[0]
        await nvidia._client.aclose()
        nvidia._client = httpx.AsyncClient(
            base_url="https://example.invalid/v1/",
            transport=httpx.MockTransport(respond),
        )

        async def execute(name: str, args: dict) -> str:
            self.assertEqual((name, args), ("web_search", {"query": "nvidia nim"}))
            return "search result"

        try:
            text, provider_name = await router.complete(
                [{"role": "user", "content": "search"}],
                [_search_tool()],
                execute,
            )
        finally:
            await _close_router(router)

        self.assertEqual((text, provider_name), ("done", "nvidia"))
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request["stream"] is False for request in requests))
        self.assertIn("tools", requests[0])
        self.assertIn("tools", requests[1])


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

    async def test_http_202_falls_back_to_groq_without_polling(self) -> None:
        nvidia_calls = 0
        groq_calls = 0

        def nvidia_respond(request: httpx.Request) -> httpx.Response:
            nonlocal nvidia_calls
            nvidia_calls += 1
            return httpx.Response(202, json={"requestId": "pending"}, request=request)

        def groq_respond(request: httpx.Request) -> httpx.Response:
            nonlocal groq_calls
            groq_calls += 1
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "fallback"}}]},
                request=request,
            )

        settings = Settings(
            _env_file=None,
            nvidia_nim_api_key="nvapi-test",
            groq_api_key="groq-test",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        nvidia, groq = router.capable_providers(requires_vision=False)
        await nvidia._client.aclose()
        await groq._client.aclose()
        nvidia._client = httpx.AsyncClient(
            base_url="https://nvidia.invalid/v1/",
            transport=httpx.MockTransport(nvidia_respond),
        )
        groq._client = httpx.AsyncClient(
            base_url="https://groq.invalid/v1/",
            transport=httpx.MockTransport(groq_respond),
        )

        async def execute(_name: str, _args: dict) -> str:
            return "unused"

        try:
            text, provider_name = await router.complete(
                [{"role": "user", "content": "hello"}],
                None,
                execute,
            )
        finally:
            await _close_router(router)

        self.assertEqual((text, provider_name), ("fallback", "groq"))
        self.assertEqual((nvidia_calls, groq_calls), (1, 1))
        self.assertEqual(router.last_fallbacks, 1)


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_nvidia_429_retry_after_cools_down_and_next_request_skips_it(self) -> None:
        nvidia_calls = 0
        groq_calls = 0

        def nvidia_respond(request: httpx.Request) -> httpx.Response:
            nonlocal nvidia_calls
            nvidia_calls += 1
            return httpx.Response(
                429,
                headers={"Retry-After": "120"},
                json={"error": {"message": "rate limited"}},
                request=request,
            )

        def groq_respond(request: httpx.Request) -> httpx.Response:
            nonlocal groq_calls
            groq_calls += 1
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "groq"}}]},
                request=request,
            )

        settings = Settings(
            _env_file=None,
            nvidia_nim_api_key="nvapi-test",
            groq_api_key="groq-test",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        nvidia, groq = router.capable_providers(requires_vision=False)
        await nvidia._client.aclose()
        await groq._client.aclose()
        nvidia._client = httpx.AsyncClient(
            base_url="https://nvidia.invalid/v1/",
            transport=httpx.MockTransport(nvidia_respond),
        )
        groq._client = httpx.AsyncClient(
            base_url="https://groq.invalid/v1/",
            transport=httpx.MockTransport(groq_respond),
        )

        async def execute(_name: str, _args: dict) -> str:
            return "unused"

        try:
            first = await router.complete(
                [{"role": "user", "content": "one"}],
                None,
                execute,
            )
            second = await router.complete(
                [{"role": "user", "content": "two"}],
                None,
                execute,
            )
        finally:
            await _close_router(router)

        self.assertEqual(first, ("groq", "groq"))
        self.assertEqual(second, ("groq", "groq"))
        self.assertEqual(nvidia_calls, 1)
        self.assertEqual(groq_calls, 2)
        self.assertFalse(nvidia.health.available())
        self.assertGreater(nvidia.health.cooldown_seconds(), 0)


def _search_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "parameters": {"type": "object", "properties": {}},
        },
    }


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
