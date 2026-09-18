"""Deterministic stale-success concurrency regressions for provider health."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.base import AllProvidersFailed, ProviderError
from app.ai.contracts import ChatRequest, ChatResponse
from app.ai.recovery import HealthScope
from app.ai.router import AIProviderRouter
from app.ai.target import provider_target_identity
from tests.provider_fakes import ScriptedProvider, noop_tool, text_request


class _BlockingRateLimitProvider(ScriptedProvider):
    """First call blocks and succeeds; second call fails with HTTP 429."""

    def __init__(
        self,
        family: str,
        *,
        model: str,
        credential_id: str,
    ) -> None:
        super().__init__(family, [])
        self.model = model
        self.credential_id = credential_id
        self.target_id = f"{family}:text:{model}:{credential_id}"
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.call_count = 0

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        self.call_count += 1
        if self.call_count == 1:
            self.first_started.set()
            await self.release_first.wait()
            return ChatResponse(content="older-success")
        if self.call_count == 2:
            raise ProviderError(
                "rate limited",
                status_code=429,
                retry_after=120.0,
                transient=True,
            )
        return ChatResponse(content="stale-target-selected")


class _BlockingSuccessThenTransportProvider(ScriptedProvider):
    """First call blocks and succeeds; second observes a transport failure."""

    def __init__(self, family: str) -> None:
        super().__init__(family, [])
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.call_count = 0

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        self.call_count += 1
        if self.call_count == 1:
            self.first_started.set()
            await self.release_first.wait()
            return ChatResponse(content="older-success")
        if self.call_count == 2:
            error = ProviderError("transport failure: read_timeout", transient=True)
            error.transport_kind = "read_timeout"
            raise error
        return ChatResponse(content="unexpected-primary-success")


class _RepeatedTransportAroundDirectSuccessProvider(ScriptedProvider):
    """Two request-A transport failures straddle request-B direct success."""

    def __init__(self, family: str) -> None:
        super().__init__(family, [])
        self.second_started = asyncio.Event()
        self.release_second = asyncio.Event()
        self.call_count = 0

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        self.call_count += 1
        if self.call_count == 1:
            error = ProviderError(
                "transport failure before direct success",
                transient=True,
            )
            error.transport_kind = "connect_timeout"
            raise error
        if self.call_count == 2:
            self.second_started.set()
            await self.release_second.wait()
            error = ProviderError(
                "transport failure after direct success",
                transient=True,
            )
            error.transport_kind = "read_timeout"
            raise error
        if self.call_count == 3:
            return ChatResponse(content="newer-direct-success")
        return ChatResponse(content="unexpected-primary-success")


class _BlockingFailureProvider(ScriptedProvider):
    """Pause one request so a concurrent scoped-health event can interleave."""

    def __init__(self, family: str) -> None:
        super().__init__(family, [])
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        self.started.set()
        await self.release.wait()
        raise ProviderError(
            "bridge unavailable",
            status_code=503,
            transient=True,
        )


def _target(
    provider: ScriptedProvider,
    *,
    model: str,
    credential_id: str,
) -> ScriptedProvider:
    provider.model = model
    provider.credential_id = credential_id
    provider.target_id = f"{provider.name}:text:{model}:{credential_id}"
    return provider


def _request() -> ChatRequest:
    return text_request()


class ProviderHealthConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_xkiro_older_success_cannot_clear_newer_credential_cooldown(self) -> None:
        raced = _BlockingRateLimitProvider(
            "xkiro",
            model="model-a",
            credential_id="cred-1",
        )
        sibling_key = _target(
            ScriptedProvider(
                "xkiro",
                [
                    ChatResponse(content="b-sibling"),
                    ChatResponse(content="c-sibling"),
                ],
            ),
            model="model-a",
            credential_id="cred-2",
        )
        same_key_other_model = _target(
            ScriptedProvider("xkiro", [ChatResponse(content="must-not-run")]),
            model="model-b",
            credential_id="cred-1",
        )
        router = AIProviderRouter(
            [raced, sibling_key, same_key_other_model],
            text_provider_order=("xkiro",),
            vision_provider_order=(),
        )
        raced_identity = provider_target_identity(raced)

        request_a = asyncio.create_task(router.complete(_request(), noop_tool))
        await raced.first_started.wait()

        request_b = await router.complete(_request(), noop_tool)
        self.assertEqual(request_b.content, "b-sibling")
        credential_health = router.scoped_health.health(
            HealthScope.CREDENTIAL,
            raced_identity,
        )
        self.assertFalse(credential_health.available())
        self.assertFalse(raced.health.available())

        raced.release_first.set()
        request_a_result = await request_a
        self.assertEqual(request_a_result.content, "older-success")

        self.assertFalse(
            credential_health.available(),
            "older success must not erase the newer xKiro credential cooldown",
        )
        self.assertFalse(
            raced.health.available(),
            "older success must not erase newer adapter-local cooldown state",
        )

        request_c = await router.complete(_request(), noop_tool)
        self.assertEqual(request_c.content, "c-sibling")
        self.assertEqual(raced.call_count, 2)
        self.assertEqual(len(same_key_other_model.requests), 0)

    async def test_chainnode_older_success_cannot_clear_newer_model_cooldown(self) -> None:
        raced = _BlockingRateLimitProvider(
            "chainnode",
            model="model-a",
            credential_id="cred-1",
        )
        same_model_other_key = _target(
            ScriptedProvider("chainnode", [ChatResponse(content="must-not-run")]),
            model="model-a",
            credential_id="cred-2",
        )
        sibling_model = _target(
            ScriptedProvider(
                "chainnode",
                [
                    ChatResponse(content="b-sibling"),
                    ChatResponse(content="c-sibling"),
                ],
            ),
            model="model-b",
            credential_id="cred-1",
        )
        router = AIProviderRouter(
            [raced, same_model_other_key, sibling_model],
            text_provider_order=("chainnode",),
            vision_provider_order=(),
        )
        raced_identity = provider_target_identity(raced)

        request_a = asyncio.create_task(router.complete(_request(), noop_tool))
        await raced.first_started.wait()

        request_b = await router.complete(_request(), noop_tool)
        self.assertEqual(request_b.content, "b-sibling")
        model_health = router.scoped_health.health(HealthScope.MODEL, raced_identity)
        self.assertFalse(model_health.available())
        self.assertFalse(raced.health.available())

        raced.release_first.set()
        request_a_result = await request_a
        self.assertEqual(request_a_result.content, "older-success")

        self.assertFalse(
            model_health.available(),
            "older success must not erase the newer Chainnode model cooldown",
        )
        self.assertFalse(
            raced.health.available(),
            "older success must not erase newer adapter-local cooldown state",
        )

        request_c = await router.complete(_request(), noop_tool)
        self.assertEqual(request_c.content, "c-sibling")
        self.assertEqual(raced.call_count, 2)
        self.assertEqual(len(same_model_other_key.requests), 0)

    async def test_older_success_cannot_erase_newer_deferred_transport_failure(self) -> None:
        primary = _BlockingSuccessThenTransportProvider("a")
        fallback = ScriptedProvider("b", [ChatResponse(content="fallback-success")])
        first_router = AIProviderRouter(
            [primary, fallback],
            text_provider_order=("a", "b"),
        )
        second_router = AIProviderRouter(
            [primary, fallback],
            text_provider_order=("a", "b"),
        )

        request_a = asyncio.create_task(first_router.complete(_request(), noop_tool))
        await primary.first_started.wait()

        request_b = await second_router.complete(_request(), noop_tool)
        self.assertEqual(request_b.content, "fallback-success")
        self.assertEqual(primary.health.consecutive_transient_failures, 1)
        self.assertIsNotNone(primary.health.last_error)

        primary.release_first.set()
        request_a_result = await request_a
        self.assertEqual(request_a_result.content, "older-success")

        self.assertEqual(primary.health.consecutive_transient_failures, 1)
        self.assertIsNotNone(primary.health.last_error)

    async def test_failure_after_new_direct_generation_refreshes_deferred_barrier(
        self,
    ) -> None:
        primary = _RepeatedTransportAroundDirectSuccessProvider("a")
        fallback = ScriptedProvider("b", [ChatResponse(content="fallback-success")])
        request_a_router = AIProviderRouter(
            [primary, fallback],
            text_provider_order=("a", "b"),
            vision_provider_order=(),
        )
        request_b_router = AIProviderRouter(
            [primary],
            text_provider_order=("a",),
            vision_provider_order=(),
        )

        request_a = asyncio.create_task(
            request_a_router.complete(_request(), noop_tool)
        )
        await primary.second_started.wait()

        request_b = await request_b_router.complete(_request(), noop_tool)
        self.assertEqual(request_b.content, "newer-direct-success")
        self.assertEqual(primary.health.deferred_barrier_generation, 1)
        self.assertEqual(primary.health.consecutive_transient_failures, 0)

        primary.release_second.set()
        request_a_result = await request_a
        self.assertEqual(request_a_result.content, "fallback-success")

        self.assertEqual(primary.call_count, 3)
        self.assertEqual(primary.health.consecutive_transient_failures, 1)
        self.assertIn("after direct success", primary.health.last_error or "")

    async def test_same_family_success_flushes_sibling_deferred_incident(self) -> None:
        first_error = ProviderError("target-one read timeout", transient=True)
        first_error.transport_kind = "read_timeout"
        target_one = _target(
            ScriptedProvider("chainnode", [first_error]),
            model="model-a",
            credential_id="cred-1",
        )
        target_two = _target(
            ScriptedProvider("chainnode", [ChatResponse(content="sibling-success")]),
            model="model-b",
            credential_id="cred-1",
        )
        vision_same_model = _target(
            ScriptedProvider(
                "chainnode",
                [
                    ProviderError(
                        "concurrent model cooldown",
                        status_code=429,
                        retry_after=120.0,
                        transient=True,
                    )
                ],
                route="vision",
                max_images=1,
            ),
            model="model-a",
            credential_id="cred-1",
        )
        vision_same_model.target_id = "chainnode:vision:model-a:cred-1"
        bridge = _target(
            _BlockingFailureProvider("bridge"),
            model="bridge-model",
            credential_id="cred-1",
        )
        router = AIProviderRouter(
            [target_one, target_two, vision_same_model, bridge],
            text_provider_order=("chainnode", "bridge"),
            vision_provider_order=("chainnode",),
        )

        request_a = asyncio.create_task(router.complete(_request(), noop_tool))
        await bridge.started.wait()

        with self.assertRaises(AllProvidersFailed):
            await router.complete(
                _request(),
                noop_tool,
                requires_vision=True,
                image_count=1,
            )
        model_health = router.scoped_health.health(
            HealthScope.MODEL,
            provider_target_identity(target_one),
        )
        self.assertFalse(model_health.available())
        self.assertEqual(target_one.health.consecutive_transient_failures, 0)
        self.assertIsNone(target_one.health.last_error)

        bridge.release.set()
        result = await request_a

        self.assertEqual(result.content, "sibling-success")
        self.assertEqual(result.provider, "chainnode")
        self.assertEqual(len(target_one.requests), 1)
        self.assertEqual(len(target_two.requests), 1)
        self.assertEqual(len(vision_same_model.requests), 1)
        self.assertEqual(len(bridge.requests), 1)
        self.assertEqual(target_one.health.consecutive_transient_failures, 1)
        self.assertIn("target-one read timeout", target_one.health.last_error or "")


if __name__ == "__main__":
    unittest.main()
