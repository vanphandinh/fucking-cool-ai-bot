"""Regression coverage for generic tool-loop recovery and fresh synthesis."""

from __future__ import annotations

import json
import unittest

import httpx

from app.ai.base import AllProvidersFailed
from app.ai.router import AIProviderRouter, CompletionResult
from tests.openai_target_fakes import make_catalog_target
from tests.provider_fakes import fetch_url_definition, text_request


class XKiroNoToolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_retry_omits_tools_and_tool_choice(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return _text_response(request, "done")

        provider = _make_target()
        await provider.aclose()
        provider._client = _mock_client(respond)
        try:
            response = await provider.chat(text_request("answer now"))
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "done")
        self.assertEqual(len(requests), 1)
        self.assertNotIn("tool_choice", requests[0])
        self.assertNotIn("tools", requests[0])

    async def test_router_recovers_from_tool_budget_with_fresh_xkiro_pass(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response(request, "call_first")
            serialized = json.dumps(payload["messages"], ensure_ascii=False)
            structured = any(
                message.get("role") == "tool" or bool(message.get("tool_calls"))
                for message in payload["messages"]
            )
            valid = (
                "tool_choice" not in payload
                and "tools" not in payload
                and not structured
                and "fetched content" in serialized
            )
            if not valid:
                return httpx.Response(
                    400,
                    json={"error": {"message": "synthesis contract violated"}},
                    request=request,
                )
            return _text_response(request, "recovered from tool result")

        provider = _make_target()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=1)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "fetched content"

        try:
            result = await router.complete(
                text_request("read this URL", tools=(fetch_url_definition(),)),
                    execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(
            result,
            CompletionResult("recovered from tool result", "xkiro", ()),
        )
        self.assertEqual(executed, ["fetch_url"])
        self.assertEqual(len(requests), 2)

    async def test_round_budget_switches_next_xkiro_turn_to_synthesis_only(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) <= 3:
                return _tool_response(request, f"call_{len(requests)}")
            return _text_response(request, "synthesized")

        provider = _make_target()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=3)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "fetched content"

        try:
            result = await router.complete(
                text_request("research HYPE price analysis", tools=(fetch_url_definition(),)),
                    execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(result, CompletionResult("synthesized", "xkiro", ()))
        self.assertEqual(executed, ["fetch_url"] * 3)
        self.assertEqual(len(requests), 4)
        self.assertTrue(all("tools" in payload for payload in requests[:3]))
        self.assertNotIn("tools", requests[3])
        self.assertNotIn("tool_choice", requests[3])

    async def test_local_tool_policy_failure_does_not_cool_down_xkiro_vision(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return _tool_response(request, f"call_{len(requests)}")

        provider = _make_vision_target()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=1)

        async def execute(_name: str, _args: dict) -> str:
            return "fetched content"

        try:
            with self.assertRaises(AllProvidersFailed) as ctx:
                await router.complete(
                    text_request("inspect and research", tools=(fetch_url_definition(),)),
                    execute,
                    requires_vision=True,
                    image_count=1,
                )
        finally:
            await provider.aclose()

        self.assertEqual(ctx.exception.fallbacks, ())
        self.assertEqual(len(requests), 2)
        self.assertIn("tools", requests[0])
        self.assertNotIn("tools", requests[1])
        self.assertNotIn("tool_choice", requests[1])
        self.assertTrue(provider.health.available())
        self.assertEqual(provider.health.consecutive_transient_failures, 0)

    async def test_xkiro_synthesis_tool_call_ends_without_same_provider_retry(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return _tool_response(request, f"xkiro_call_{len(requests)}")

        provider = _make_target()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=3)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "fetched content"

        try:
            with self.assertRaises(AllProvidersFailed) as ctx:
                await router.complete(
                    text_request("summarize HYPE price analysis", tools=(fetch_url_definition(),)),
                    execute,
                )
        finally:
            await provider.aclose()

        self.assertEqual(ctx.exception.fallbacks, ())
        self.assertEqual(executed, ["fetch_url"] * 3)
        self.assertEqual(len(requests), 4)
        self.assertTrue(all("tools" in payload for payload in requests[:3]))
        self.assertNotIn("tools", requests[3])
        self.assertNotIn("tool_choice", requests[3])
        self.assertTrue(provider.health.available())
        self.assertEqual(provider.health.consecutive_transient_failures, 0)

    async def test_total_call_budget_switches_to_synthesis_at_exact_limit(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return _tool_response_many(request, "call", 12)
            return _text_response(request, "twelve-call synthesis")

        provider = _make_target()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=10)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "fetched content"

        try:
            result = await router.complete(
                text_request("research broadly", tools=(fetch_url_definition(),)),
                    execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(
            result,
            CompletionResult("twelve-call synthesis", "xkiro", ()),
        )
        self.assertEqual(executed, ["fetch_url"] * 12)
        self.assertEqual(len(requests), 2)
        self.assertNotIn("tools", requests[1])
        self.assertNotIn("tool_choice", requests[1])

    async def test_oversized_tool_batch_is_suppressed_before_synthesis(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return _tool_response_many(request, "oversized", 13)
            return _text_response(request, "synthesized without overflow")

        provider = _make_target()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=10)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "should not execute"

        try:
            result = await router.complete(
                text_request(
                    "research without exceeding hard limit",
                    tools=(fetch_url_definition(),),
                ),
                    execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(
            result,
            CompletionResult("synthesized without overflow", "xkiro", ()),
        )
        self.assertEqual(executed, [])
        self.assertEqual(len(requests), 2)
        self.assertNotIn("tools", requests[1])
        self.assertNotIn("tool_choice", requests[1])

    async def test_zero_round_budget_never_sends_tool_schema(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return _text_response(request, "plain only")

        provider = _make_target()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=0)

        async def execute(_name: str, _args: dict) -> str:
            self.fail("tool executor must not run when max_tool_rounds=0")

        try:
            result = await router.complete(
                text_request("answer without tools", tools=(fetch_url_definition(),)),
                    execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(result, CompletionResult("plain only", "xkiro", ()))
        self.assertEqual(len(requests), 1)
        self.assertNotIn("tools", requests[0])
        self.assertNotIn("tool_choice", requests[0])


def _make_target():
    return make_catalog_target("xkiro", model="test-text-model")


def _make_vision_target():
    return make_catalog_target(
        "xkiro",
        route="vision",
        model="test-vision-model",
        target_id="xkiro:vision:m1:c1",
    )


def _mock_client(responder) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.xkiro.com/v1/",
        transport=httpx.MockTransport(responder),
    )


def _text_response(request: httpx.Request, content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": content}}
            ]
        },
        request=request,
    )


def _tool_response(request: httpx.Request, call_id: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": "fetch_url",
                                    "arguments": '{"url":"https://example.com"}',
                                },
                            }
                        ],
                    }
                }
            ]
        },
        request=request,
    )


def _tool_response_many(
    request: httpx.Request,
    prefix: str,
    count: int,
) -> httpx.Response:
    calls = [
        {
            "id": f"{prefix}_{index}",
            "type": "function",
            "function": {
                "name": "fetch_url",
                "arguments": f'{{"url":"https://example.com/{index}"}}',
            },
        }
        for index in range(count)
    ]
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": calls,
                    }
                }
            ]
        },
        request=request,
    )


if __name__ == "__main__":
    unittest.main()
