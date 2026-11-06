"""Generic ordered-provider routing regressions."""

from __future__ import annotations

import unittest
from dataclasses import replace

from app.ai.base import AllProvidersFailed, NoCapableProvider, ProviderError
from app.ai.contracts import ChatResponse
from app.ai.router import AIProviderRouter, CompletionResult
from tests.provider_fakes import (
    ScriptedProvider,
    fetch_url_definition,
    noop_tool,
    text_request,
    tool_response,
)


class OrderedProviderRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_conversation_session_id_survives_every_turn(self) -> None:
        provider = ScriptedProvider(
            "first",
            [ChatResponse(content="one"), ChatResponse(content="two")],
        )
        router = AIProviderRouter([provider], text_provider_order=("first",))
        request = replace(text_request("hi"), session_id="ses_conversation_1")
        await router.complete(request, noop_tool)
        await router.complete(request, noop_tool)
        self.assertEqual(
            [captured.session_id for captured in provider.requests],
            ["ses_conversation_1", "ses_conversation_1"],
        )

    async def test_request_without_conversation_id_gets_unique_session(self) -> None:
        provider = ScriptedProvider(
            "first", [ChatResponse(content="one"), ChatResponse(content="two")]
        )
        router = AIProviderRouter([provider], text_provider_order=("first",))
        await router.complete(text_request("hi"), noop_tool)
        await router.complete(text_request("hi"), noop_tool)
        first, second = (captured.session_id for captured in provider.requests)
        self.assertIsNotNone(first)
        self.assertTrue(first.startswith("ses_"))
        self.assertNotEqual(first, second)

    async def test_anonymous_session_id_survives_tool_rounds(self) -> None:
        provider = ScriptedProvider(
            "first",
            [tool_response(1), ChatResponse(content="done")],
        )
        provider.supports_tools = True
        router = AIProviderRouter([provider], max_tool_rounds=1)
        await router.complete(
            text_request("hi", tools=(fetch_url_definition(),)),
            noop_tool,
        )
        first, second = (captured.session_id for captured in provider.requests)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)

    async def test_first_success_does_not_call_second_provider(self) -> None:
        first = ScriptedProvider("first", [ChatResponse(content="one")])
        second = ScriptedProvider("second", [ChatResponse(content="unused")])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        result = await router.complete(text_request("hi"), noop_tool
        )
        self.assertEqual(result, CompletionResult("one", "first", ()))
        self.assertEqual(second.requests, [])

    async def test_failed_first_provider_falls_back_in_order(self) -> None:
        first = ScriptedProvider("first", [ProviderError("timeout")])
        second = ScriptedProvider("second", [ChatResponse(content="ok")])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        result = await router.complete(text_request("hi"), noop_tool
        )
        self.assertEqual(result, CompletionResult("ok", "second", ("second",)))

    async def test_permanent_provider_local_failure_can_fall_back(self) -> None:
        first = ScriptedProvider(
            "first",
            [ProviderError("bad provider response", transient=False)],
        )
        second = ScriptedProvider("second", [ChatResponse(content="ok")])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        result = await router.complete(text_request("hi"), noop_tool
        )
        self.assertEqual(result, CompletionResult("ok", "second", ("second",)))

    async def test_rate_limit_health_is_isolated_to_failed_provider(self) -> None:
        first = ScriptedProvider(
            "first",
            [
                ProviderError(
                    "quota",
                    status_code=429,
                    retry_after=60,
                    transient=True,
                )
            ],
        )
        second = ScriptedProvider("second", [ChatResponse(content="ok")])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        result = await router.complete(text_request("hi"), noop_tool
        )
        self.assertEqual(result.provider, "second")
        self.assertFalse(first.health.available())
        self.assertTrue(second.health.available())

    async def test_unhealthy_provider_is_skipped_without_fallback_count(self) -> None:
        first = ScriptedProvider("first", [ChatResponse(content="unused")])
        first.health.disabled = True
        second = ScriptedProvider("second", [ChatResponse(content="ok")])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        result = await router.complete(text_request("hi"), noop_tool
        )
        self.assertEqual(result, CompletionResult("ok", "second", ()))
        self.assertEqual(first.requests, [])

    async def test_no_capable_provider_does_not_call_text_slot_for_vision(self) -> None:
        text = ScriptedProvider("text", [ChatResponse(content="unused")])
        router = AIProviderRouter(
            [text],
            text_provider_order=("text",),
            vision_provider_order=("text",),
        )
        with self.assertRaises(NoCapableProvider):
            await router.complete(text_request("image"), noop_tool,
                requires_vision=True,
                image_count=1,
            )
        self.assertEqual(text.requests, [])

    async def test_all_attempted_providers_fail_with_fallback_metadata(self) -> None:
        first = ScriptedProvider("first", [ProviderError("one", transient=False)])
        second = ScriptedProvider("second", [ProviderError("two", transient=False)])
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("first", "second"),
        )
        with self.assertRaises(AllProvidersFailed) as ctx:
            await router.complete(text_request("hi"), noop_tool
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
            await router.complete(text_request("hi"), noop_tool
            )
        self.assertEqual(second.requests, [])

    def test_duplicate_exact_target_is_rejected(self) -> None:
        first = ScriptedProvider("openrouter", [], target_id="openrouter:text:m1:c1")
        duplicate = ScriptedProvider("openrouter", [], target_id="openrouter:text:m1:c1")

        with self.assertRaisesRegex(ValueError, "duplicate provider target"):
            AIProviderRouter(
                [first, duplicate],
                text_provider_order=("openrouter",),
            )

    def test_same_name_text_and_vision_slots_are_allowed(self) -> None:
        text = ScriptedProvider("openrouter", [], route="text")
        vision = ScriptedProvider("openrouter", [], route="vision", max_images=1)

        router = AIProviderRouter(
            [text, vision],
            text_provider_order=("openrouter",),
            vision_provider_order=("openrouter",),
        )

        self.assertEqual(
            router.capable_providers(requires_vision=False, image_count=0),
            [text],
        )
        self.assertEqual(
            router.capable_providers(requires_vision=True, image_count=1),
            [vision],
        )


if __name__ == "__main__":
    unittest.main()
