"""Regression tests for the B.AI free-model provider integration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from types import SimpleNamespace
import unittest

import httpx

import app.ai.bai as bai
from app.ai.router import build_provider_router
from app.config import Settings


class BaiProviderModuleTests(unittest.TestCase):
    def test_bai_provider_module_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.ai.bai"))

    def test_documented_zero_credit_models_are_explicit(self) -> None:
        self.assertEqual(
            bai.SUPPORTED_PROMO_MODELS,
            frozenset({"qwen3.8-flash", "mimo-v2.5", "hy3", "glm-5.3-flash"}),
        )

    def test_only_documented_multimodal_models_are_marked_for_vision(self) -> None:
        self.assertTrue(bai.model_supports_vision("qwen3.8-flash"))
        self.assertTrue(bai.model_supports_vision("mimo-v2.5"))
        self.assertTrue(bai.model_supports_vision("glm-5.3-flash"))
        self.assertFalse(bai.model_supports_vision("hy3"))
        self.assertFalse(bai.model_supports_vision("unknown"))

    def test_text_factory_uses_chat_completions_base_and_bai_timeout(self) -> None:
        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="qwen3.8-flash",
            bai_vision_model="qwen3.8-flash",
            bai_request_timeout_sec=30.0,
        )
        provider = bai.make_bai_provider(settings)
        try:
            self.assertEqual(provider.name, "bai")
            self.assertEqual(provider.model, "qwen3.8-flash")
            self.assertEqual(str(provider._client.base_url), "https://api.b.ai/v1/")
            self.assertEqual(provider.capabilities.route, "text")
            self.assertFalse(provider.capabilities.supports_vision)
            self.assertEqual(provider.capabilities.max_images, 0)
        finally:
            asyncio.run(provider.aclose())

    def test_factory_rejects_models_outside_documented_promotion(self) -> None:
        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="not-promoted",
            bai_vision_model="qwen3.8-flash",
            bai_request_timeout_sec=30.0,
        )
        with self.assertRaisesRegex(ValueError, "B.AI model"):
            bai.make_bai_provider(settings)

    def test_vision_factory_rejects_text_only_hy3(self) -> None:
        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="qwen3.8-flash",
            bai_vision_model="hy3",
            bai_request_timeout_sec=30.0,
        )
        with self.assertRaisesRegex(ValueError, "vision"):
            bai.make_bai_provider(settings, name="bai_vision", vision=True)

    def test_vision_factory_starts_with_one_image_capability(self) -> None:
        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="qwen3.8-flash",
            bai_vision_model="mimo-v2.5",
            bai_request_timeout_sec=30.0,
        )
        provider = bai.make_bai_provider(settings, name="bai_vision", vision=True)
        try:
            self.assertEqual(provider.model, "mimo-v2.5")
            self.assertEqual(provider.capabilities.route, "vision")
            self.assertTrue(provider.capabilities.supports_vision)
            self.assertEqual(provider.capabilities.max_images, 1)
        finally:
            asyncio.run(provider.aclose())


class BaiRoutingTests(unittest.TestCase):
    def test_unordered_bai_config_does_not_affect_existing_router(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="b",
            bai_text_model="not-promoted",
            bai_vision_model="hy3",
            groq_api_key="g",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        try:
            self.assertEqual(
                [provider.name for provider in router.capable_providers(requires_vision=False)],
                ["groq"],
            )
        finally:
            asyncio.run(_close_router(router))

    def test_bai_is_available_only_when_explicitly_ordered(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="b",
            groq_api_key="g",
            text_provider_order="bai,groq",
            vision_enabled=False,
        )
        self.assertEqual(settings.configured_provider_names, ["bai", "groq"])
        router = build_provider_router(settings)
        try:
            self.assertEqual(
                [provider.name for provider in router.capable_providers(requires_vision=False)],
                ["bai", "groq"],
            )
            self.assertEqual(
                [provider.model for provider in router.capable_providers(requires_vision=False)],
                ["qwen3.8-flash", "openai/gpt-oss-120b"],
            )
        finally:
            asyncio.run(_close_router(router))

    def test_bai_vision_slot_is_filtered_above_one_image(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="b",
            gemini_api_key="g",
            vision_provider_order="bai,gemini",
        )
        self.assertEqual(settings.configured_vision_provider_names, ["bai", "gemini"])
        router = build_provider_router(settings)
        try:
            self.assertEqual(
                [
                    provider.name
                    for provider in router.capable_providers(
                        requires_vision=True,
                        image_count=1,
                    )
                ],
                ["bai_vision", "gemini_vision"],
            )
            self.assertEqual(
                [
                    provider.name
                    for provider in router.capable_providers(
                        requires_vision=True,
                        image_count=2,
                    )
                ],
                ["gemini_vision"],
            )
        finally:
            asyncio.run(_close_router(router))


class BaiWireContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_request_uses_documented_fields_only(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                request=request,
            )

        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="qwen3.8-flash",
            bai_vision_model="qwen3.8-flash",
            bai_request_timeout_sec=30.0,
        )
        provider = bai.make_bai_provider(settings)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://api.b.ai/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            response = await provider.chat(
                [{"role": "user", "content": "hello"}],
                [_search_tool()],
            )
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "ok")
        self.assertEqual(len(requests), 1)
        self.assertEqual(set(requests[0]), {"model", "messages", "tools"})
        self.assertNotIn("enable_thinking", requests[0])
        self.assertNotIn("reasoning_effort", requests[0])
        self.assertNotIn("thinking", requests[0])

    async def test_reasoning_content_is_preserved_for_tool_replay(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "reasoning_content": "opaque-reasoning",
                                "tool_calls": [
                                    {
                                        "id": "call_search",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query":"latest"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                request=request,
            )

        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="mimo-v2.5",
            bai_vision_model="qwen3.8-flash",
            bai_request_timeout_sec=30.0,
        )
        provider = bai.make_bai_provider(settings)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://api.b.ai/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            response = await provider.chat(
                [{"role": "user", "content": "latest?"}],
                [_search_tool()],
            )
        finally:
            await provider.aclose()

        self.assertEqual(response.assistant_metadata, {"reasoning_content": "opaque-reasoning"})
        self.assertEqual(response.tool_calls[0].name, "web_search")


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
