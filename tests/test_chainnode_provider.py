"""Regression tests for the Chainnode OpenAI-compatible provider integration."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from types import SimpleNamespace
import unittest

import httpx

import app.ai.chainnode as chainnode
from app.ai.router import build_provider_router
from app.config import Settings


TEXT_MODEL = "cl/cline-free/deepseek-v4.1-flash"
VISION_MODEL = "cl/cline-free/muse-spark-1.3-contributor"


class ChainnodeProviderModuleTests(unittest.TestCase):
    def test_chainnode_provider_module_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.ai.chainnode"))

    def test_text_factory_uses_configured_base_model_and_timeout(self) -> None:
        settings = SimpleNamespace(
            chainnode_api_key="test-key",
            chainnode_base_url="https://dn.chainno.de/v1",
            chainnode_text_model="cl/z-ai/glm-5.3-flash",
            chainnode_request_timeout_sec=25.0,
        )
        provider = chainnode.make_chainnode_provider(settings)
        try:
            self.assertEqual(provider.name, "chainnode")
            self.assertEqual(provider.model, "cl/z-ai/glm-5.3-flash")
            self.assertEqual(str(provider._client.base_url), "https://dn.chainno.de/v1/")
            self.assertEqual(provider._client.timeout.read, 25.0)
            self.assertEqual(provider.capabilities.route, "text")
            self.assertFalse(provider.capabilities.supports_vision)
            self.assertEqual(provider.capabilities.max_images, 0)
        finally:
            asyncio.run(provider.aclose())

    def test_text_and_vision_factories_use_distinct_models(self) -> None:
        settings = SimpleNamespace(
            chainnode_api_key="test-key",
            chainnode_base_url="https://dn.chainno.de/v1",
            chainnode_text_model=TEXT_MODEL,
            chainnode_vision_model=VISION_MODEL,
            chainnode_request_timeout_sec=25.0,
        )
        text = chainnode.make_chainnode_provider(settings, vision=False)
        vision = chainnode.make_chainnode_provider(settings, vision=True)
        try:
            self.assertEqual(text.model, TEXT_MODEL)
            self.assertEqual(text.capabilities.route, "text")
            self.assertFalse(text.capabilities.supports_vision)
            self.assertEqual(text.capabilities.max_images, 0)
            self.assertEqual(vision.model, VISION_MODEL)
            self.assertEqual(vision.capabilities.route, "vision")
            self.assertTrue(vision.capabilities.supports_vision)
            self.assertEqual(vision.capabilities.max_images, 1)
        finally:
            asyncio.run(text.aclose())
            asyncio.run(vision.aclose())


class ChainnodeDeploymentConfigTests(unittest.TestCase):
    def test_defaults_keep_chainnode_primary_with_xkiro_fallback(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.chainnode_api_key, "")
        self.assertEqual(settings.chainnode_base_url, "https://dn.chainno.de/v1")
        self.assertEqual(settings.chainnode_text_model, "")
        self.assertEqual(settings.chainnode_vision_model, "")
        self.assertEqual(settings.chainnode_request_timeout_sec, 60.0)
        self.assertEqual(settings.text_provider_order_list, ["chainnode", "xkiro"])
        self.assertEqual(settings.vision_provider_order_list, ["chainnode", "xkiro"])

    def test_chainnode_vision_order_requires_explicit_model(self) -> None:
        with self.assertRaisesRegex(ValueError, "CHAINNODE_VISION_MODEL"):
            Settings(
                _env_file=None,
                chainnode_api_key="test-key",
                text_provider_order="xkiro",
                vision_enabled=True,
                vision_provider_order="chainnode,xkiro",
            )

    def test_disabled_vision_does_not_require_chainnode_vision_model(self) -> None:
        settings = Settings(
            _env_file=None,
            chainnode_api_key="test-key",
            text_provider_order="xkiro",
            vision_enabled=False,
            vision_provider_order="chainnode,xkiro",
        )
        self.assertFalse(settings.vision_enabled)

    def test_chainnode_slot_requires_selection_and_explicit_model(self) -> None:
        disabled = build_provider_router(
            Settings(
                _env_file=None,
                chainnode_api_key="test-key",
                text_provider_order="xkiro",
                vision_provider_order="",
            )
        )
        enabled = build_provider_router(
            Settings(
                _env_file=None,
                chainnode_api_key="test-key",
                chainnode_text_model=TEXT_MODEL,
                xkiro_api_key="test-xkiro-key",
                xkiro_text_model="test-xkiro-text",
                text_provider_order="chainnode,xkiro",
                vision_provider_order="",
            )
        )
        try:
            self.assertEqual(disabled.configured_provider_names(False), ())
            self.assertEqual(
                enabled.configured_provider_names(False),
                ("chainnode", "xkiro"),
            )
        finally:
            asyncio.run(_close_router(disabled))
            asyncio.run(_close_router(enabled))

    def test_chainnode_builds_selected_text_and_vision_slots_independently(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                chainnode_api_key="test-key",
                chainnode_text_model=TEXT_MODEL,
                chainnode_vision_model=VISION_MODEL,
                text_provider_order="chainnode,xkiro",
                vision_provider_order="chainnode,xkiro",
            )
        )
        try:
            chainnode_slots = [p for p in router.providers if p.name == "chainnode"]
            by_route = {p.capabilities.route: p for p in chainnode_slots}
            self.assertEqual(set(by_route), {"text", "vision"})
            self.assertEqual(by_route["text"].model, TEXT_MODEL)
            self.assertEqual(by_route["vision"].model, VISION_MODEL)
            self.assertEqual(router.configured_provider_names(False), ("chainnode",))
            self.assertEqual(router.configured_provider_names(True), ("chainnode",))
        finally:
            asyncio.run(_close_router(router))

    def test_chainnode_vision_can_be_enabled_without_chainnode_text(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                chainnode_api_key="test-key",
                chainnode_vision_model=VISION_MODEL,
                text_provider_order="xkiro",
                vision_provider_order="chainnode,xkiro",
            )
        )
        try:
            chainnode_slots = [p for p in router.providers if p.name == "chainnode"]
            self.assertEqual(len(chainnode_slots), 1)
            self.assertEqual(chainnode_slots[0].capabilities.route, "vision")
            self.assertEqual(chainnode_slots[0].model, VISION_MODEL)
            self.assertEqual(router.configured_provider_names(False), ())
            self.assertEqual(router.configured_provider_names(True), ("chainnode",))
        finally:
            asyncio.run(_close_router(router))


class ChainnodeWireContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_request_uses_shared_openai_compat_contract(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                request=request,
            )

        settings = SimpleNamespace(
            chainnode_api_key="test-key",
            chainnode_base_url="https://dn.chainno.de/v1",
            chainnode_text_model=TEXT_MODEL,
            chainnode_request_timeout_sec=30.0,
        )
        provider = chainnode.make_chainnode_provider(settings)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://dn.chainno.de/v1/",
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
        self.assertEqual(set(requests[0]), {"model", "messages", "tools", "stream"})
        self.assertEqual(requests[0]["model"], TEXT_MODEL)
        self.assertIs(requests[0]["stream"], False)
        self.assertNotIn("reasoning_effort", requests[0])
        self.assertNotIn("thinking", requests[0])
        self.assertNotIn("enable_thinking", requests[0])

    async def test_vision_request_preserves_runtime_multimodal_payload(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "vision ok"}}
                    ]
                },
                request=request,
            )

        settings = SimpleNamespace(
            chainnode_api_key="test-key",
            chainnode_base_url="https://dn.chainno.de/v1",
            chainnode_text_model=TEXT_MODEL,
            chainnode_vision_model=VISION_MODEL,
            chainnode_request_timeout_sec=30.0,
        )
        provider = chainnode.make_chainnode_provider(settings, vision=True)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://dn.chainno.de/v1/",
            transport=httpx.MockTransport(respond),
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                    },
                ],
            }
        ]
        try:
            response = await provider.chat(messages)
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "vision ok")
        self.assertEqual(requests[0]["model"], VISION_MODEL)
        self.assertIs(requests[0]["stream"], False)
        self.assertEqual(requests[0]["messages"], messages)


def _search_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
