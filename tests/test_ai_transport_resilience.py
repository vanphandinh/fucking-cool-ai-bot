"""Regression coverage for shared AI HTTP transport failures."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx

from app.ai.base import OpenAICompatProvider, ProviderError
from app.config import Settings


class AITransportResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_uses_split_phase_timeouts(self) -> None:
        provider = _provider(timeout=60.0)
        try:
            timeout = provider._client.timeout
            self.assertEqual(timeout.connect, 8.0)
            self.assertEqual(timeout.read, 60.0)
            self.assertEqual(timeout.write, 20.0)
            self.assertEqual(timeout.pool, 5.0)
        finally:
            await provider.aclose()

    def test_ai_provider_read_timeouts_default_to_60_seconds(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(_env_file=None)

        self.assertEqual(settings.bai_request_timeout_sec, 60.0)
        self.assertEqual(settings.chainnode_request_timeout_sec, 60.0)

    async def test_empty_read_timeout_reports_type_and_configured_phase_timeout(self) -> None:
        attempts = 0

        def fail(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("", request=request)

        provider = _provider(timeout=30.0)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            timeout=30.0,
            transport=httpx.MockTransport(fail),
        )
        try:
            with self.assertRaises(ProviderError) as raised:
                await provider.chat([{"role": "user", "content": "hello"}])
        finally:
            await provider.aclose()

        message = str(raised.exception)
        self.assertEqual(attempts, 1)
        self.assertIn("ReadTimeout", message)
        self.assertIn("read_timeout=30s", message)
        self.assertNotIn("lỗi mạng ()", message)

    async def test_connect_timeout_fails_fast_before_router_fallback(self) -> None:
        attempts = 0

        def fail(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectTimeout("", request=request)

        provider = _provider(timeout=60.0)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            timeout=30.0,
            transport=httpx.MockTransport(fail),
        )
        try:
            with self.assertRaises(ProviderError) as raised:
                await provider.chat([{"role": "user", "content": "hello"}])
        finally:
            await provider.aclose()

        message = str(raised.exception)
        self.assertEqual(attempts, 1)
        self.assertIn("ConnectTimeout", message)
        self.assertIn("connect_timeout=8s", message)

    async def test_connect_error_retries_once_before_failing_over(self) -> None:
        attempts = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("temporary connect failure", request=request)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "recovered"}}
                    ]
                },
                request=request,
            )

        provider = _provider(timeout=60.0)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            timeout=30.0,
            transport=httpx.MockTransport(respond),
        )
        try:
            response = await provider.chat([{"role": "user", "content": "hello"}])
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "recovered")
        self.assertEqual(attempts, 2)


def _provider(*, timeout: float) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name="test",
        base_url="https://example.test/v1",
        api_key="secret",
        model="test-model",
        timeout=timeout,
    )


if __name__ == "__main__":
    unittest.main()
