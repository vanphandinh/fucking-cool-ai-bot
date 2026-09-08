"""Regression tests for NVIDIA NIM latency tuning and diagnostics."""

from __future__ import annotations

import json
import unittest

import httpx

from app.ai.base import OpenAICompatProvider, ProviderError
from app.ai.nvidia import make_nvidia_provider
from app.config import Settings


class NvidiaLatencyDefaultsTests(unittest.TestCase):
    def test_defaults_use_lightning_with_bounded_reasoning(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(
            settings.nvidia_nim_text_model,
            "nvidia/nemotron-3.5-lightning-30b-a3b",
        )
        self.assertTrue(settings.nvidia_nim_enable_thinking)
        self.assertEqual(settings.nvidia_nim_max_tokens, 4096)
        self.assertEqual(settings.nvidia_nim_thinking_token_budget, 2048)


class NvidiaLatencyPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_lightning_request_uses_supported_reasoning_budget_parameter(self) -> None:
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
        provider = make_nvidia_provider(settings)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.invalid/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            response = await provider.chat([{"role": "user", "content": "hello"}])
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "ok")
        self.assertEqual(len(requests), 1)
        payload = requests[0]
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["max_tokens"], 4096)
        self.assertEqual(payload["reasoning_budget"], 2048)
        self.assertNotIn("thinking_token_budget", payload)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": True})

    async def test_fast_mode_omits_reasoning_budget(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "fast"}}]},
                request=request,
            )

        settings = Settings(
            _env_file=None,
            nvidia_nim_api_key="nvapi-test",
            nvidia_nim_enable_thinking=False,
            vision_enabled=False,
        )
        provider = make_nvidia_provider(settings)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.invalid/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            response = await provider.chat([{"role": "user", "content": "hello"}])
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "fast")
        payload = requests[0]
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("reasoning_budget", payload)
        self.assertNotIn("thinking_token_budget", payload)


class NetworkErrorDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_timeout_reports_exception_type_and_elapsed_time(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("", request=request)

        provider = OpenAICompatProvider(
            "nvidia",
            "https://example.invalid/v1",
            "fake",
            "model",
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

        message = str(ctx.exception)
        self.assertIn("ReadTimeout", message)
        self.assertIn("after", message)
        self.assertNotIn("lỗi mạng ()", message)
        self.assertTrue(ctx.exception.transient)


if __name__ == "__main__":
    unittest.main()
