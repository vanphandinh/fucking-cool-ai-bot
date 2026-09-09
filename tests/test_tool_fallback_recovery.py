"""Regression coverage for tool-loop recovery across B.AI and Gemini."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

import httpx

from app.ai.bai import make_bai_provider
from app.ai.base import AllProvidersFailed, OpenAICompatProvider
from app.ai.gemini import make_gemini_provider
from app.ai.router import AIProviderRouter


class BaiNoToolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_retry_explicitly_forces_tool_choice_none(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
                request=request,
            )

        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="qwen3.8-flash",
            bai_vision_model="qwen3.8-flash",
            bai_request_timeout_sec=30.0,
        )
        provider = make_bai_provider(settings)
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://api.b.ai/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            response = await provider.chat([{"role": "user", "content": "answer now"}], None)
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "done")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get("tool_choice"), "none")
        self.assertNotIn("tools", requests[0])


class GeminiCrossProviderFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_bai_tool_history_can_fallback_to_gemini_without_signature_400(self) -> None:
        bai_requests: list[dict] = []
        gemini_requests: list[dict] = []

        def bai_respond(request: httpx.Request) -> httpx.Response:
            bai_requests.append(json.loads(request.content))
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
                                        "id": f"call_{len(bai_requests)}",
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

        def gemini_respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            gemini_requests.append(payload)
            historical_calls = [
                call
                for message in payload["messages"]
                if message.get("role") == "assistant"
                for call in message.get("tool_calls") or []
            ]
            signature = None
            if historical_calls:
                signature = (
                    historical_calls[0]
                    .get("extra_content", {})
                    .get("google", {})
                    .get("thought_signature")
                )
            if signature != "skip_thought_signature_validator":
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "code": 400,
                            "message": (
                                "Function call is missing a thought_signature in functionCall parts."
                            ),
                            "status": "INVALID_ARGUMENT",
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
                request=request,
            )

        bai = OpenAICompatProvider("bai", "https://bai.test/v1", "fake", "qwen3.8-flash")
        await bai.aclose()
        bai._client = httpx.AsyncClient(
            base_url="https://bai.test/v1/",
            transport=httpx.MockTransport(bai_respond),
        )

        gemini = make_gemini_provider(
            SimpleNamespace(
                gemini_api_key="fake",
                gemini_model="gemini-3.8-flash",
                request_timeout_sec=30.0,
                max_images_per_request=3,
            )
        )
        await gemini.aclose()
        gemini._client = httpx.AsyncClient(
            base_url="https://gemini.test/v1/",
            transport=httpx.MockTransport(gemini_respond),
        )
        router = AIProviderRouter([bai, gemini], max_tool_rounds=1)

        async def execute(_name: str, _args: dict) -> str:
            return "fetched content"

        try:
            try:
                result = await router.complete(
                    [{"role": "user", "content": "read this URL"}],
                    [_fetch_url_tool()],
                    execute,
                )
            except AllProvidersFailed as exc:
                self.fail(f"cross-provider fallback should succeed: {exc}")
        finally:
            await bai.aclose()
            await gemini.aclose()

        self.assertEqual(result, ("done", "gemini"))
        self.assertGreaterEqual(len(bai_requests), 2)
        self.assertEqual(len(gemini_requests), 1)
        assistant_calls = [
            call
            for message in gemini_requests[0]["messages"]
            if message.get("role") == "assistant"
            for call in message.get("tool_calls") or []
        ]
        self.assertTrue(assistant_calls)
        self.assertEqual(
            assistant_calls[0]["extra_content"]["google"]["thought_signature"],
            "skip_thought_signature_validator",
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
