"""Focused regressions for request-wide synthesis-only tool-budget state."""

from __future__ import annotations

import json
import unittest

import httpx

from app.ai.base import OpenAICompatProvider
from app.ai.router import AIProviderRouter


class ToolBudgetSynthesisTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_batch_closes_tools_for_fallback_provider(self) -> None:
        first_requests: list[dict] = []
        second_requests: list[dict] = []

        def first_respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            first_requests.append(payload)
            if len(first_requests) == 1:
                return _tool_response_many(request, 9)
            return _tool_response_many(request, 1)

        def second_respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            second_requests.append(payload)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
                request=request,
            )

        first = _provider("first", first_respond)
        second = _provider("second", second_respond)
        router = AIProviderRouter([first, second], max_tool_rounds=10)

        async def execute(_name: str, _args: dict) -> str:
            self.fail("oversized or synthesis tool calls must not execute")

        try:
            result = await router.complete(
                [{"role": "user", "content": "research broadly"}],
                [_fetch_url_tool()],
                execute,
            )
        finally:
            await first.aclose()
            await second.aclose()

        self.assertEqual(result, ("done", "second"))
        self.assertEqual(len(first_requests), 2)
        self.assertIn("tools", first_requests[0])
        self.assertNotIn("tools", first_requests[1])
        self.assertEqual(len(second_requests), 1)
        self.assertNotIn("tools", second_requests[0])


def _provider(name: str, responder) -> OpenAICompatProvider:
    provider = OpenAICompatProvider(name, f"https://{name}.test/v1", "fake", "model")
    provider._client = httpx.AsyncClient(
        base_url=f"https://{name}.test/v1/",
        transport=httpx.MockTransport(responder),
    )
    return provider


def _tool_response_many(request: httpx.Request, count: int) -> httpx.Response:
    tool_calls = [
        {
            "id": f"call_{index}",
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
                        "tool_calls": tool_calls,
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
