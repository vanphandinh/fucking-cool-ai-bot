"""Regression tests for the xKiro OpenAI-compatible provider integration."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest

import httpx

import app.ai.xkiro as xkiro


class XKiroProviderTests(unittest.TestCase):
    def test_text_factory_uses_xkiro_contract(self) -> None:
        settings = SimpleNamespace(
            xkiro_api_key="test-key",
            xkiro_text_model="test-text-model",
            xkiro_vision_model="test-vision-model",
            xkiro_request_timeout_sec=25.0,
        )
        provider = xkiro.make_xkiro_provider(settings)
        try:
            self.assertEqual(provider.name, "xkiro")
            self.assertEqual(provider.model, "test-text-model")
            self.assertEqual(str(provider._client.base_url), "https://api.xkiro.com/v1/")
            self.assertEqual(provider._client.timeout.read, 25.0)
            self.assertEqual(provider.capabilities.route, "text")
            self.assertFalse(provider.capabilities.supports_vision)
            self.assertEqual(provider.capabilities.max_images, 0)
            self.assertIs(provider.explicit_stream, False)
            self.assertFalse(hasattr(provider, "force_tool_choice_none_when_no_tools"))
        finally:
            asyncio.run(provider.aclose())

    def test_vision_factory_uses_one_image_capability(self) -> None:
        settings = SimpleNamespace(
            xkiro_api_key="test-key",
            xkiro_text_model="test-text-model",
            xkiro_vision_model="test-vision-model",
            xkiro_request_timeout_sec=25.0,
        )
        provider = xkiro.make_xkiro_provider(settings, vision=True)
        try:
            self.assertEqual(provider.model, "test-vision-model")
            self.assertEqual(provider.capabilities.route, "vision")
            self.assertTrue(provider.capabilities.supports_vision)
            self.assertEqual(provider.capabilities.max_images, 1)
        finally:
            asyncio.run(provider.aclose())

    def test_missing_route_model_is_rejected(self) -> None:
        settings = SimpleNamespace(
            xkiro_api_key="test-key",
            xkiro_text_model="",
            xkiro_vision_model="",
            xkiro_request_timeout_sec=25.0,
        )
        with self.assertRaisesRegex(ValueError, "XKIRO_TEXT_MODEL"):
            xkiro.make_xkiro_provider(settings)
        with self.assertRaisesRegex(ValueError, "XKIRO_VISION_MODEL"):
            xkiro.make_xkiro_provider(settings, vision=True)


class XKiroWireContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_request_with_tools_is_non_stream_openai_compatible(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                request=request,
            )

        settings = SimpleNamespace(
            xkiro_api_key="test-key",
            xkiro_text_model="test-text-model",
            xkiro_vision_model="test-vision-model",
            xkiro_request_timeout_sec=30.0,
        )
        provider = xkiro.make_xkiro_provider(settings)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(respond),
        )
        tool = {
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
        try:
            response = await provider.chat(
                [{"role": "user", "content": "hello"}],
                [tool],
            )
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "ok")
        self.assertEqual(len(requests), 1)
        self.assertEqual(set(requests[0]), {"model", "messages", "tools", "stream"})
        self.assertIs(requests[0]["stream"], False)
        for forbidden in ("tool_choice", "reasoning_effort", "thinking", "enable_thinking"):
            self.assertNotIn(forbidden, requests[0])


if __name__ == "__main__":
    unittest.main()
