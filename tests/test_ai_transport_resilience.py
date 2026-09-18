"""Regression coverage for shared AI HTTP transport failures."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import httpx

from tests.provider_fakes import text_request

from app.ai import base as ai_base
from app.ai.base import ProviderError
from app.ai.capabilities import ProviderCapabilities
from app.ai.drivers.openai_chat import OpenAIChatDriver, OpenAIChatTarget
from app.ai.drivers.registry import get_driver
from app.ai.recovery import RecoveryPolicy
from app.ai.target import ProviderTargetIdentity, TargetSpec
from app.ai.runtime_config import provider_runtime
from app.config import Settings


class DriverRegistryTests(unittest.TestCase):
    def test_openai_chat_driver_is_registered(self) -> None:
        self.assertIsInstance(get_driver("openai-chat"), OpenAIChatDriver)

    def test_unknown_driver_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown AI driver"):
            get_driver("missing")


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

        self.assertIsNone(provider_runtime(settings, "xkiro").request_timeout_sec)
        self.assertIsNone(provider_runtime(settings, "chainnode").request_timeout_sec)

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
                await provider.chat(text_request("hello"))
        finally:
            await provider.aclose()

        self.assertEqual(attempts, 1)
        kind_type = getattr(ai_base, "TransportFailureKind", None)
        self.assertIsNotNone(kind_type)
        self.assertIsInstance(getattr(raised.exception, "transport_kind", None), kind_type)
        self.assertEqual(raised.exception.transport_kind, expected_kind)
        return str(raised.exception)


def _provider(*, timeout: float) -> OpenAIChatTarget:
    spec = TargetSpec(
        identity=ProviderTargetIdentity(
            family="test",
            route="text",
            model="test-model",
            credential_id="cred-1",
            target_id="test:text:m1:c1",
        ),
        driver="openai-chat",
        capabilities=ProviderCapabilities(route="text"),
        base_url="https://example.test/v1",
        request_timeout_sec=timeout,
        recovery_policy=RecoveryPolicy(),
        driver_options={"explicit_stream": False},
    )
    return OpenAIChatDriver().build_target(spec, credential="test-key")


if __name__ == "__main__":
    unittest.main()
