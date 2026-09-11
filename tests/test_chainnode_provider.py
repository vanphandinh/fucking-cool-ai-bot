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


class ChainnodeProviderModuleTests(unittest.TestCase):
    def test_chainnode_provider_module_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.ai.chainnode"))

    def test_text_factory_uses_configured_base_model_and_timeout(self) -> None:
        settings = SimpleNamespace(
            chainnode_api_key="secret",
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
            self.assertFalse(provider.force_tool_choice_none_when_no_tools)
        finally:
            asyncio.run(provider.aclose())


class ChainnodeDeploymentConfigTests(unittest.TestCase):
    def test_chainnode_defaults_do_not_change_current_bai_production_order(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.chainnode_api_key, "")
        self.assertEqual(settings.chainnode_base_url, "https://dn.chainno.de/v1")
        self.assertEqual(settings.chainnode_text_model, "")
        self.assertEqual(settings.chainnode_request_timeout_sec, 30.0)
        self.assertEqual(settings.text_provider_order_list, ["bai"])

    def test_chainnode_slot_requires_key_order_and_explicit_model(self) -> None:
        disabled = build_provider_router(
            Settings(
                _env_file=None,
                chainnode_api_key="c",
                text_provider_order="bai",
            )
        )
        enabled = build_provider_router(
            Settings(
                _env_file=None,
                chainnode_api_key="c",
                chainnode_text_model="cl/z-ai/glm-5.3-flash",
                bai_api_key="b",
                text_provider_order="chainnode,bai",
            )
        )
        try:
            self.assertEqual(disabled.configured_provider_names(False), ())
            self.assertEqual(
                enabled.configured_provider_names(False),
                ("chainnode", "bai"),
            )
        finally:
            asyncio.run(_close_router(disabled))
            asyncio.run(_close_router(enabled))


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
            chainnode_api_key="secret",
            chainnode_base_url="https://dn.chainno.de/v1",
            chainnode_text_model="cl/z-ai/glm-5.3-flash",
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
        self.assertEqual(requests[0]["model"], "cl/z-ai/glm-5.3-flash")
        self.assertIs(requests[0]["stream"], False)
        self.assertNotIn("reasoning_effort", requests[0])
        self.assertNotIn("thinking", requests[0])
        self.assertNotIn("enable_thinking", requests[0])


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
