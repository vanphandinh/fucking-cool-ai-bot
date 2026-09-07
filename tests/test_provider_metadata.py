"""Provider-specific assistant metadata regressions."""

from __future__ import annotations

import json
import unittest

import httpx

from app.ai.base import OpenAICompatProvider
from app.ai.router import AIProviderRouter


class OpenRouterReasoningMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_details_are_replayed_during_tool_call(self) -> None:
        reasoning_details = [
            {
                "type": "reasoning.encrypted",
                "data": "opaque-reasoning-block",
                "id": "reasoning-1",
                "format": "anthropic-claude-v1",
                "index": 0,
            }
        ]
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "reasoning_details": reasoning_details,
                                    "tool_calls": [
                                        {
                                            "id": "call_search",
                                            "type": "function",
                                            "function": {
                                                "name": "web_search",
                                                "arguments": '{"query":"latest"}',
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                    request=request,
                )

            replayed = payload["messages"][-2]
            if replayed.get("reasoning_details") != reasoning_details:
                return httpx.Response(
                    400,
                    json={"error": {"message": "missing reasoning_details"}},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
                request=request,
            )

        provider = OpenAICompatProvider(
            "openrouter", "https://example.org/v1", "fake", "openrouter/free"
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.org/v1/", transport=httpx.MockTransport(respond)
        )
        router = AIProviderRouter([provider], max_tool_rounds=2)

        async def execute(_name: str, _args: dict) -> str:
            return "search result"

        try:
            text, name = await router.complete(
                [{"role": "user", "content": "latest?"}],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual((text, name), ("done", "openrouter"))
        self.assertEqual(len(requests), 2)


if __name__ == "__main__":
    unittest.main()
