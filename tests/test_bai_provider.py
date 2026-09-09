"""Regression tests for the B.AI provider integration."""

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


class BaiDeploymentConfigTests(unittest.TestCase):
    def test_bai_defaults_remain_qwen_flash(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.bai_text_model, "qwen3.8-flash")
        self.assertEqual(settings.bai_vision_model, "qwen3.8-flash")
        self.assertEqual(settings.max_images_per_request, 1)
        self.assertEqual(settings.text_provider_order_list, ["bai", "aurora"])
        self.assertEqual(settings.vision_provider_order_list, ["bai"])

    def test_current_deployment_reports_bai_only_when_configured(self) -> None:
        router = build_provider_router(Settings(_env_file=None, bai_api_key="b"))
        try:
            self.assertEqual(router.configured_provider_names(False), ("bai",))
            self.assertEqual(router.configured_provider_names(True), ("bai",))
        finally:
            asyncio.run(_close_router(router))

    def test_missing_bai_key_builds_no_configured_provider_slots(self) -> None:
        router = build_provider_router(Settings(_env_file=None))
        self.assertEqual(router.configured_provider_names(False), ())
        self.assertEqual(router.configured_provider_names(True), ())

    def test_vision_disable_switch_removes_only_vision_slot(self) -> None:
        router = build_provider_router(
            Settings(_env_file=None, bai_api_key="b", vision_enabled=False)
        )
        try:
            self.assertEqual(router.configured_provider_names(False), ("bai",))
            self.assertEqual(router.configured_provider_names(True), ())
        finally:
            asyncio.run(_close_router(router))


class BaiOnlyRouterTests(unittest.TestCase):
    def test_router_builds_only_bai_text_slot(self) -> None:
        settings = Settings(_env_file=None, bai_api_key="b", vision_enabled=False)
        router = build_provider_router(settings)
        try:
            providers = router.capable_providers(requires_vision=False)
            self.assertEqual([p.name for p in providers], ["bai"])
            self.assertEqual([p.model for p in providers], ["qwen3.8-flash"])
        finally:
            asyncio.run(_close_router(router))

    def test_router_builds_bai_vision_family_slot_for_one_image(self) -> None:
        settings = Settings(_env_file=None, bai_api_key="b")
        router = build_provider_router(settings)
        try:
            one = router.capable_providers(requires_vision=True, image_count=1)
            two = router.capable_providers(requires_vision=True, image_count=2)
            self.assertEqual([p.name for p in one], ["bai"])
            self.assertEqual([p.capabilities.route for p in one], ["vision"])
            self.assertEqual(two, [])
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

        self.assertEqual(
            response.assistant_metadata,
            {"reasoning_content": "opaque-reasoning"},
        )
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
