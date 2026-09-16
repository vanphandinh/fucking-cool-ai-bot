"""Regression coverage for shared AI HTTP transport failures."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx

from app.ai import base as ai_base
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

        self.assertEqual(settings.xkiro_request_timeout_sec, 60.0)
        self.assertEqual(settings.chainnode_request_timeout_sec, 60.0)

    async def test_connect_error_is_classified_without_adapter_retry(self) -> None:
        message = await self._assert_transport_failure(
            httpx.ConnectError,
            "connect_error",
            timeout=60.0,
        )
        self.assertIn("ConnectError", message)

    async def test_connect_timeout_is_classified_without_adapter_retry(self) -> None:
        message = await self._assert_transport_failure(
            httpx.ConnectTimeout,
            "connect_timeout",
            timeout=60.0,
        )
        self.assertIn("ConnectTimeout", message)
        self.assertIn("connect_timeout=8s", message)

    async def test_read_timeout_is_classified_without_adapter_retry(self) -> None:
        message = await self._assert_transport_failure(
            httpx.ReadTimeout,
            "read_timeout",
            timeout=30.0,
        )
        self.assertIn("ReadTimeout", message)
        self.assertIn("read_timeout=30s", message)
        self.assertNotIn("lỗi mạng ()", message)

    async def test_write_timeout_is_classified_without_adapter_retry(self) -> None:
        message = await self._assert_transport_failure(
            httpx.WriteTimeout,
            "write_timeout",
            timeout=60.0,
        )
        self.assertIn("WriteTimeout", message)
        self.assertIn("write_timeout=20s", message)

    async def test_pool_timeout_is_classified_without_adapter_retry(self) -> None:
        message = await self._assert_transport_failure(
            httpx.PoolTimeout,
            "pool_timeout",
            timeout=60.0,
        )
        self.assertIn("PoolTimeout", message)
        self.assertIn("pool_timeout=5s", message)

    async def _assert_transport_failure(
        self,
        error_type: type[httpx.HTTPError],
        expected_kind: str,
        *,
        timeout: float,
    ) -> str:
        attempts = 0

        def fail(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise error_type("", request=request)

        provider = _provider(timeout=timeout)
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

        self.assertEqual(attempts, 1)
        kind_type = getattr(ai_base, "TransportFailureKind", None)
        self.assertIsNotNone(kind_type)
        self.assertIsInstance(getattr(raised.exception, "transport_kind", None), kind_type)
        self.assertEqual(raised.exception.transport_kind, expected_kind)
        return str(raised.exception)


def _provider(*, timeout: float) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name="test",
        base_url="https://example.test/v1",
        api_key="test-key",
        model="test-model",
        timeout=timeout,
    )


if __name__ == "__main__":
    unittest.main()
