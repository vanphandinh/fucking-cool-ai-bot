"""Regression tests for the xKiro OpenAI-compatible provider integration."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest

import httpx

import app.ai.xkiro as xkiro


def _settings(*, timeout: float = 25.0) -> SimpleNamespace:
    return SimpleNamespace(
        xkiro_api_keys_list=["test-key"],
        xkiro_base_url="https://api.xkiro.com/v1",
        xkiro_text_models_list=["test-text-model"],
        xkiro_vision_models_list=["test-vision-model"],
        xkiro_request_timeout_sec=timeout,
    )


class XKiroProviderTests(unittest.TestCase):
    def test_text_factory_uses_xkiro_contract(self) -> None:
        provider = xkiro.make_xkiro_provider(_settings())
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

    def test_text_factory_uses_configured_base_url(self) -> None:
        settings = _settings()
        settings.xkiro_base_url = "https://gateway.example/xkiro/v1"
        provider = xkiro.make_xkiro_provider(settings)
        try:
            self.assertEqual(
                str(provider._client.base_url),
                "https://gateway.example/xkiro/v1/",
            )
        finally:
            asyncio.run(provider.aclose())

    def test_vision_factory_uses_one_image_capability(self) -> None:
        provider = xkiro.make_xkiro_provider(_settings(), vision=True)
        try:
            self.assertEqual(provider.model, "test-vision-model")
            self.assertEqual(provider.capabilities.route, "vision")
            self.assertTrue(provider.capabilities.supports_vision)
            self.assertEqual(provider.capabilities.max_images, 1)
        finally:
            asyncio.run(provider.aclose())

    def test_missing_route_model_is_rejected(self) -> None:
        settings = _settings()
        settings.xkiro_text_models_list = []
        settings.xkiro_vision_models_list = []
        with self.assertRaisesRegex(ValueError, "XKIRO_TEXT_MODELS"):
            xkiro.make_xkiro_provider(settings)
        with self.assertRaisesRegex(ValueError, "XKIRO_VISION_MODELS"):
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

        provider = xkiro.make_xkiro_provider(_settings(timeout=30.0))
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
