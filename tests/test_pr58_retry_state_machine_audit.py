"""Concurrency/retry state-machine audit regressions for PR #58."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import math
import unittest

import httpx

from app.ai.base import AllProvidersFailed, ProviderError
from app.ai.recovery import HealthScope
from app.ai.router import AIProviderRouter
from app.ai.target import provider_target_identity
from tests.openai_target_fakes import make_catalog_target
from tests.provider_fakes import noop_tool, text_request


class RetryAfterHealthBoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_negative_retry_after_cannot_bypass_default_cooldown(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"retry-after": "-1"},
                json={"error": {"message": "rate limited"}},
                request=request,
            )

        provider = make_catalog_target(
            "chainnode",
            model="retry-model",
            credential="test-key",
            target_id="chainnode:text:retry-negative:c1",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            transport=httpx.MockTransport(respond),
        )
        router = AIProviderRouter(
            [provider],
            text_provider_order=("chainnode",),
            vision_provider_order=(),
        )

        try:
            with self.assertRaises(AllProvidersFailed):
                await router.complete(text_request("hello"), noop_tool)
        finally:
            await provider.aclose()

        identity = provider_target_identity(provider)
        model_health = router.scoped_health.health(HealthScope.MODEL, identity)
        self.assertGreater(provider.health.cooldown_seconds(), 0)
        self.assertGreater(model_health.cooldown_seconds(), 0)

    async def test_http_date_retry_after_is_converted_to_delay_seconds(self) -> None:
        retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"retry-after": format_datetime(retry_at, usegmt=True)},
                json={"error": {"message": "rate limited"}},
                request=request,
            )

        provider = make_catalog_target(
            "chainnode",
            model="retry-date-model",
            credential="test-key",
            target_id="chainnode:text:retry-date:c1",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            with self.assertRaises(ProviderError) as raised:
                await provider.chat(text_request("hello"))
        finally:
            await provider.aclose()

        self.assertIsNotNone(raised.exception.retry_after)
        assert raised.exception.retry_after is not None
        self.assertGreater(raised.exception.retry_after, 240.0)
        self.assertLessEqual(raised.exception.retry_after, 300.0)

    async def test_nonfinite_retry_after_cannot_create_infinite_cooldown(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"retry-after": "inf"},
                json={"error": {"message": "rate limited"}},
                request=request,
            )

        provider = make_catalog_target(
            "chainnode",
            model="retry-model",
            credential="test-key",
            target_id="chainnode:text:retry:c1",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.test/v1/",
            transport=httpx.MockTransport(respond),
        )
        router = AIProviderRouter(
            [provider],
            text_provider_order=("chainnode",),
            vision_provider_order=(),
        )

        try:
            with self.assertRaises(AllProvidersFailed):
                await router.complete(text_request("hello"), noop_tool)
        finally:
            await provider.aclose()

        identity = provider_target_identity(provider)
        model_health = router.scoped_health.health(HealthScope.MODEL, identity)
        self.assertTrue(math.isfinite(provider.health.cooldown_until))
        self.assertTrue(math.isfinite(model_health.cooldown_until))


if __name__ == "__main__":
    unittest.main()
