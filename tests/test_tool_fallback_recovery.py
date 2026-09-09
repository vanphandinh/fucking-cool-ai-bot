"""Regression coverage for B.AI tool-loop recovery and synthesis."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

import httpx

from app.ai.bai import make_bai_provider
from app.ai.base import AllProvidersFailed
from app.ai.router import AIProviderRouter


class BaiNoToolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_retry_explicitly_forces_tool_choice_none(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return _text_response(request, "done")

        provider = _make_bai()
        await provider.aclose()
        provider._client = _mock_client(respond)
        try:
            response = await provider.chat(
                [{"role": "user", "content": "answer now"}],
                None,
            )
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "done")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get("tool_choice"), "none")
        self.assertNotIn("tools", requests[0])

    async def test_router_recovers_from_tool_budget_with_fresh_bai_pass(self) -> None:
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
                payload.get("tool_choice") == "none"
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

        provider = _make_bai()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=1)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "fetched content"

        try:
            result = await router.complete(
                [{"role": "user", "content": "read this URL"}],
                [_fetch_url_tool()],
                execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(result, ("recovered from tool result", "bai"))
        self.assertEqual(executed, ["fetch_url"])
        self.assertEqual(len(requests), 2)

    async def test_round_budget_switches_next_bai_turn_to_synthesis_only(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) <= 3:
                return _tool_response(request, f"call_{len(requests)}")
            return _text_response(request, "synthesized")

        provider = _make_bai()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=3)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "fetched content"

        try:
            result = await router.complete(
                [{"role": "user", "content": "research HYPE price analysis"}],
                [_fetch_url_tool()],
                execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(result, ("synthesized", "bai"))
        self.assertEqual(executed, ["fetch_url"] * 3)
        self.assertEqual(len(requests), 4)
        self.assertTrue(all("tools" in payload for payload in requests[:3]))
        self.assertNotIn("tools", requests[3])
        self.assertEqual(requests[3].get("tool_choice"), "none")

    async def test_local_tool_policy_failure_does_not_cool_down_bai_vision(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return _tool_response(request, f"call_{len(requests)}")

        provider = _make_bai_vision()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=1)

        async def execute(_name: str, _args: dict) -> str:
            return "fetched content"

        try:
            with self.assertRaises(AllProvidersFailed):
                await router.complete(
                    [{"role": "user", "content": "inspect and research"}],
                    [_fetch_url_tool()],
                    execute,
                    requires_vision=True,
                    image_count=1,
                )
        finally:
            await provider.aclose()

        self.assertEqual(len(requests), 2)
        self.assertIn("tools", requests[0])
        self.assertNotIn("tools", requests[1])
        self.assertEqual(requests[1].get("tool_choice"), "none")
        self.assertTrue(provider.health.available())
        self.assertEqual(provider.health.consecutive_transient_failures, 0)

    async def test_bai_synthesis_tool_call_ends_without_same_provider_retry(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return _tool_response(request, f"bai_call_{len(requests)}")

        provider = _make_bai()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=3)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "fetched content"

        try:
            with self.assertRaises(AllProvidersFailed):
                await router.complete(
                    [{"role": "user", "content": "summarize HYPE price analysis"}],
                    [_fetch_url_tool()],
                    execute,
                )
        finally:
            await provider.aclose()

        self.assertEqual(executed, ["fetch_url"] * 3)
        self.assertEqual(len(requests), 4)
        self.assertTrue(all("tools" in payload for payload in requests[:3]))
        self.assertNotIn("tools", requests[3])
        self.assertEqual(requests[3].get("tool_choice"), "none")
        self.assertTrue(provider.health.available())
        self.assertEqual(provider.health.consecutive_transient_failures, 0)

    async def test_total_call_budget_switches_to_synthesis_at_exact_limit(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return _tool_response_many(request, "call", 8)
            return _text_response(request, "eight-call synthesis")

        provider = _make_bai()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=10)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "fetched content"

        try:
            result = await router.complete(
                [{"role": "user", "content": "research broadly"}],
                [_fetch_url_tool()],
                execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(result, ("eight-call synthesis", "bai"))
        self.assertEqual(executed, ["fetch_url"] * 8)
        self.assertEqual(len(requests), 2)
        self.assertNotIn("tools", requests[1])

    async def test_oversized_tool_batch_is_suppressed_before_synthesis(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                return _tool_response_many(request, "oversized", 9)
            return _text_response(request, "synthesized without overflow")

        provider = _make_bai()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=10)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "should not execute"

        try:
            result = await router.complete(
                [{"role": "user", "content": "research without exceeding hard limit"}],
                [_fetch_url_tool()],
                execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(result, ("synthesized without overflow", "bai"))
        self.assertEqual(executed, [])
        self.assertEqual(len(requests), 2)

    async def test_zero_round_budget_never_sends_tool_schema(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return _text_response(request, "plain only")

        provider = _make_bai()
        await provider.aclose()
        provider._client = _mock_client(respond)
        router = AIProviderRouter([provider], max_tool_rounds=0)

        async def execute(_name: str, _args: dict) -> str:
            self.fail("tool executor must not run when max_tool_rounds=0")

        try:
            result = await router.complete(
                [{"role": "user", "content": "answer without tools"}],
                [_fetch_url_tool()],
                execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(result, ("plain only", "bai"))
        self.assertEqual(len(requests), 1)
        self.assertNotIn("tools", requests[0])
        self.assertEqual(requests[0].get("tool_choice"), "none")


def _bai_settings() -> SimpleNamespace:
    return SimpleNamespace(
        bai_api_key="secret",
        bai_text_model="qwen3.8-flash",
        bai_vision_model="qwen3.8-flash",
        bai_request_timeout_sec=30.0,
    )


def _make_bai():
    return make_bai_provider(_bai_settings())


def _make_bai_vision():
    return make_bai_provider(_bai_settings(), name="bai_vision", vision=True)


def _mock_client(responder) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.b.ai/v1/",
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


def _fetch_url_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
