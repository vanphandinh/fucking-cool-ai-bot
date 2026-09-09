"""Generic ordered-provider routing regressions."""

from __future__ import annotations

import unittest

from app.ai.base import AllProvidersFailed, ChatResponse, ProviderError
from app.ai.router import AIProviderRouter, CompletionResult
from tests.provider_fakes import ScriptedProvider, noop_tool


class OrderedProviderRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_first_provider_falls_back_in_order(self) -> None:
        first = ScriptedProvider("first", [ProviderError("timeout")])
        second = ScriptedProvider("second", [ChatResponse(content="ok")])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        result = await router.complete(
            [{"role": "user", "content": "hi"}], None, noop_tool
        )
        self.assertEqual(result, CompletionResult("ok", "second", ("second",)))

    async def test_unhealthy_provider_is_skipped_without_fallback_count(self) -> None:
        first = ScriptedProvider("first", [ChatResponse(content="unused")])
        first.health.disabled = True
        second = ScriptedProvider("second", [ChatResponse(content="ok")])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        result = await router.complete(
            [{"role": "user", "content": "hi"}], None, noop_tool
        )
        self.assertEqual(result, CompletionResult("ok", "second", ()))
        self.assertEqual(first.calls, [])

    async def test_all_attempted_providers_fail_with_fallback_metadata(self) -> None:
        first = ScriptedProvider("first", [ProviderError("one", transient=False)])
        second = ScriptedProvider("second", [ProviderError("two", transient=False)])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        with self.assertRaises(AllProvidersFailed) as ctx:
            await router.complete(
                [{"role": "user", "content": "hi"}], None, noop_tool
            )
        self.assertEqual(ctx.exception.fallbacks, ("second",))

    async def test_runtime_error_from_provider_does_not_try_next_provider(self) -> None:
        first = ScriptedProvider("first", [RuntimeError("internal failure")])
        second = ScriptedProvider("second", [ChatResponse(content="unused")])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        with self.assertRaisesRegex(RuntimeError, "internal failure"):
            await router.complete(
                [{"role": "user", "content": "hi"}], None, noop_tool
            )
        self.assertEqual(second.calls, [])


if __name__ == "__main__":
    unittest.main()
