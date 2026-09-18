"""Causal shared-health regressions for deferred provider transport failures."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.base import AllProvidersFailed, ProviderError
from app.ai.contracts import ChatMessage, ChatRequest, ChatResponse, TextPart
from app.ai.router import AIProviderRouter
from tests.provider_fakes import ScriptedProvider, noop_tool


class ProviderRetryHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_deferred_failure_after_newer_success_is_ignored(self) -> None:
        provider = ScriptedProvider(
            "a",
            [_transport_error("read_timeout"), ChatResponse(content="newer success")],
        )
        fallback = _GateSuccessProvider("b", expected_calls=1)
        first_router = _router(provider, fallback)
        second_router = AIProviderRouter([provider], text_provider_order=("a",))

        first_task = asyncio.create_task(
            first_router.complete(_request("first"), noop_tool)
        )
        await fallback.all_started.wait()

        second = await second_router.complete(_request("second"), noop_tool)
        self.assertEqual(second.content, "newer success")

        fallback.release.set()
        first = await first_task
        self.assertEqual(first.content, "fallback success")
        self.assertEqual(provider.health.consecutive_transient_failures, 0)
        self.assertIsNone(provider.health.last_error)
        self.assertEqual(provider.health.cooldown_seconds(), 0)

    async def test_stale_deferred_failure_after_newer_429_is_ignored(self) -> None:
        provider = ScriptedProvider(
            "a",
            [
                _transport_error("read_timeout"),
                ProviderError(
                    "newer rate limit",
                    status_code=429,
                    retry_after=120.0,
                    transient=True,
                ),
            ],
        )
        provider.health.consecutive_transient_failures = 1
        fallback = _GateSuccessProvider("b", expected_calls=1)
        first_router = _router(provider, fallback)
        second_router = AIProviderRouter([provider], text_provider_order=("a",))

        first_task = asyncio.create_task(
            first_router.complete(_request("first"), noop_tool)
        )
        await fallback.all_started.wait()

        with self.assertRaises(AllProvidersFailed):
            await second_router.complete(_request("second"), noop_tool)

        fallback.release.set()
        first = await first_task
        self.assertEqual(first.content, "fallback success")
        self.assertGreaterEqual(provider.health.cooldown_seconds(), 110)
        self.assertEqual(provider.health.last_error, "newer rate limit")
        self.assertEqual(provider.health.consecutive_transient_failures, 1)

    async def test_two_unresolved_concurrent_failures_both_count(self) -> None:
        provider = ScriptedProvider(
            "a",
            [_transport_error("read_timeout"), _transport_error("read_timeout")],
        )
        fallback = _GateSuccessProvider("b", expected_calls=2)
        first_router = _router(provider, fallback)
        second_router = _router(provider, fallback)

        first_task = asyncio.create_task(
            first_router.complete(_request("first"), noop_tool)
        )
        second_task = asyncio.create_task(
            second_router.complete(_request("second"), noop_tool)
        )
        await fallback.all_started.wait()

        fallback.release.set()
        first, second = await asyncio.gather(first_task, second_task)

        self.assertEqual(first.content, "fallback success")
        self.assertEqual(second.content, "fallback success")
        self.assertEqual(provider.health.consecutive_transient_failures, 2)
        self.assertFalse(provider.health.available())
        self.assertGreater(provider.health.cooldown_seconds(), 0)


class _GateSuccessProvider(ScriptedProvider):
    def __init__(self, name: str, *, expected_calls: int) -> None:
        super().__init__(
            name,
            [ChatResponse(content="fallback success") for _ in range(expected_calls)],
        )
        self.expected_calls = expected_calls
        self.started_calls = 0
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.started_calls += 1
        if self.started_calls >= self.expected_calls:
            self.all_started.set()
        await self.release.wait()
        return await super().chat(request)


def _transport_error(kind: str) -> ProviderError:
    error = ProviderError(f"transport failure: {kind}", transient=True)
    error.transport_kind = kind
    return error


def _router(
    provider: ScriptedProvider,
    fallback: ScriptedProvider,
) -> AIProviderRouter:
    return AIProviderRouter(
        [provider, fallback],
        text_provider_order=("a", "b"),
    )


def _request(label: str) -> ChatRequest:
    return ChatRequest(messages=(ChatMessage("user", (TextPart(label),)),))


if __name__ == "__main__":
    unittest.main()
