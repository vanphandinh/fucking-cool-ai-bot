"""Focused regressions for request-wide synthesis-only tool-budget state."""

from __future__ import annotations

import json
import unittest

import httpx

from app.ai.base import AllProvidersFailed, OpenAICompatProvider
from app.ai.router import AIProviderRouter


class ToolBudgetSynthesisTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_batch_closes_tools_and_synthesis_violation_is_terminal(
        self,
    ) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response_many(request, 9)
            return _tool_response_many(request, 1)

        provider = _provider("bai", respond)
        router = AIProviderRouter([provider], max_tool_rounds=10)

        async def execute(_name: str, _args: dict) -> str:
            self.fail("oversized or synthesis tool calls must not execute")

        try:
            with self.assertRaises(AllProvidersFailed):
                await router.complete(
                    [{"role": "user", "content": "research broadly"}],
                    [_fetch_url_tool()],
                    execute,
                )
        finally:
            await provider.aclose()

        self.assertEqual(len(requests), 2)
        self.assertIn("tools", requests[0])
        self.assertNotIn("tools", requests[1])


def _provider(name: str, responder) -> OpenAICompatProvider:
    provider = OpenAICompatProvider(
        name,
        f"https://{name}.test/v1",
        "fake",
        "model",
    )
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
