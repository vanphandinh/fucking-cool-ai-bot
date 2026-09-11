"""Regression coverage for shared AI HTTP transport failures."""

from __future__ import annotations

import unittest

import httpx

from app.ai.base import OpenAICompatProvider, ProviderError


class AITransportResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_read_timeout_reports_type_and_configured_phase_timeout(self) -> None:
        def fail(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("", request=request)

        provider = _provider()
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
        self.assertIn("ReadTimeout", message)
        self.assertIn("read_timeout=30s", message)
        self.assertNotIn("lỗi mạng ()", message)

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

        provider = _provider()
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


def _provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name="test",
        base_url="https://example.test/v1",
        api_key="secret",
        model="test-model",
        timeout=30.0,
    )


if __name__ == "__main__":
    unittest.main()
