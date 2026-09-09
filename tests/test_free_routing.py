"""Regression tests for free-first AI provider routing."""

from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from app.ai.base import OpenAICompatProvider
from app.ai.router import AIProviderRouter, build_provider_router
from app.config import Settings


class FreeFirstDefaultsTests(unittest.TestCase):
    def test_defaults_prefer_free_tier_models_and_conserve_quota(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.bai_text_model, "qwen3.8-flash")
        self.assertEqual(settings.bai_vision_model, "qwen3.8-flash")
        self.assertEqual(settings.groq_model, "openai/gpt-oss-120b")
        self.assertEqual(settings.cloudflare_text_model, "@cf/zai-org/glm-4.7-flash")
        self.assertEqual(settings.openrouter_model, "openrouter/free")
        self.assertEqual(settings.gemini_model, "gemini-3.8-flash")
        self.assertEqual(
            settings.text_provider_order,
            "bai,aurora,gemini,groq,cloudflare,openrouter",
        )
        self.assertEqual(
            settings.vision_provider_order,
            "bai,gemini,groq_qwen38,cloudflare,groq_qwen36",
        )
        self.assertEqual(settings.max_context_turns, 6)
        self.assertEqual(settings.max_tool_rounds, 2)

    def test_reported_text_providers_match_effective_order(self) -> None:
        settings = Settings(
            _env_file=None,
            gemini_api_key="g",
            groq_api_key="q",
            openrouter_api_key="o",
            cloudflare_account_id="account",
            cloudflare_api_token="token",
            text_provider_order="cloudflare,groq,gemini,openrouter",
        )

        self.assertEqual(
            settings.configured_provider_names,
            ["cloudflare", "groq", "gemini", "openrouter"],
        )

        missing_cloudflare_credentials = Settings(
            _env_file=None,
            groq_api_key="q",
            text_provider_order="cloudflare,groq",
        )
        self.assertEqual(missing_cloudflare_credentials.configured_provider_names, ["groq"])


class FreeFirstRouterTests(unittest.TestCase):
    def test_text_router_uses_default_priority_order_and_models(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="b",
            gemini_api_key="g",
            groq_api_key="q",
            openrouter_api_key="o",
            cloudflare_account_id="account",
            cloudflare_api_token="token",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        try:
            text = router.capable_providers(requires_vision=False)
            self.assertEqual(
                [provider.name for provider in text],
                ["bai", "gemini", "groq", "cloudflare_text", "openrouter"],
            )
            self.assertEqual(
                [provider.model for provider in text],
                [
                    "qwen3.8-flash",
                    "gemini-3.8-flash",
                    "openai/gpt-oss-120b",
                    "@cf/zai-org/glm-4.7-flash",
                    "openrouter/free",
                ],
            )
        finally:
            asyncio.run(_close_router(router))

    def test_vision_router_uses_default_priority_order(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="b",
            gemini_api_key="g",
            groq_api_key="q",
            cloudflare_account_id="account",
            cloudflare_api_token="token",
        )
        router = build_provider_router(settings)
        try:
            vision = router.capable_providers(requires_vision=True, image_count=1)
            self.assertEqual(
                [provider.name for provider in vision],
                ["bai_vision", "gemini_vision", "groq_qwen38", "cloudflare", "groq_qwen36"],
            )
        finally:
            asyncio.run(_close_router(router))


class ProviderMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_call_replays_gemini_thought_signature_verbatim(self) -> None:
        signature = "opaque-signature"
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
                                    "tool_calls": [
                                        {
                                            "id": "call_search",
                                            "type": "function",
                                            "function": {
                                                "name": "web_search",
                                                "arguments": '{"query":"latest"}',
                                            },
                                            "extra_content": {
                                                "google": {"thought_signature": signature}
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                    request=request,
                )

            replayed = payload["messages"][-2]["tool_calls"][0]
            if replayed.get("extra_content") != {
                "google": {"thought_signature": signature}
            }:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "Function call is missing a thought_signature"
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
                request=request,
            )

        provider = OpenAICompatProvider(
            "gemini", "https://example.org/v1", "fake", "gemini-3.8-flash"
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
                [_search_tool()],
                execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual((text, name), ("done", "gemini"))
        self.assertEqual(len(requests), 2)

    async def test_provider_specific_metadata_is_removed_before_cross_provider_fallback(
        self,
    ) -> None:
        signature = "gemini-only"
        first_requests: list[dict] = []
        second_requests: list[dict] = []

        def first_respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            first_requests.append(payload)
            if len(first_requests) == 1:
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
                                            "id": "call_search",
                                            "type": "function",
                                            "function": {
                                                "name": "web_search",
                                                "arguments": '{"query":"latest"}',
                                            },
                                            "extra_content": {
                                                "google": {"thought_signature": signature}
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                    request=request,
                )
            return httpx.Response(
                500,
                json={"error": {"message": "upstream failed"}},
                request=request,
            )

        def second_respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            second_requests.append(payload)
            for message in payload["messages"]:
                for tool_call in message.get("tool_calls") or []:
                    if "extra_content" in tool_call:
                        return httpx.Response(
                            400,
                            json={"error": {"message": "unknown field extra_content"}},
                            request=request,
                        )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "fallback done"}}
                    ]
                },
                request=request,
            )

        first = OpenAICompatProvider(
            "gemini", "https://first.example/v1", "fake", "gemini-3.8-flash"
        )
        second = OpenAICompatProvider(
            "groq", "https://second.example/v1", "fake", "openai/gpt-oss-120b"
        )
        await first.aclose()
        await second.aclose()
        first._client = httpx.AsyncClient(
            base_url="https://first.example/v1/",
            transport=httpx.MockTransport(first_respond),
        )
        second._client = httpx.AsyncClient(
            base_url="https://second.example/v1/",
            transport=httpx.MockTransport(second_respond),
        )
        router = AIProviderRouter([first, second], max_tool_rounds=2)

        async def execute(_name: str, _args: dict) -> str:
            return "search result"

        try:
            text, name = await router.complete(
                [{"role": "user", "content": "latest?"}],
                [_search_tool()],
                execute,
            )
        finally:
            await first.aclose()
            await second.aclose()

        self.assertEqual((text, name), ("fallback done", "groq"))
        self.assertEqual(len(second_requests), 1)

    async def test_text_encoded_tool_call_is_executed_not_leaked(self) -> None:
        requests: list[dict] = []
        executed: list[tuple[str, dict]] = []
        leaked = (
            "<tool_call>web_search\n"
            "<arg_key>query</arg_key>\n"
            "<arg_value>Việt Nam công ty khai thác xuất khẩu đất hiếm 2024 2025</arg_value>\n"
            "</tool_call>"
        )

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            content = leaked if len(requests) == 1 else "Kết quả đã được tổng hợp."
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": content}}]},
                request=request,
            )

        provider = OpenAICompatProvider(
            "groq", "https://example.org/v1", "fake", "openai/gpt-oss-120b"
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.org/v1/", transport=httpx.MockTransport(respond)
        )
        router = AIProviderRouter([provider], max_tool_rounds=2)

        async def execute(name: str, args: dict) -> str:
            executed.append((name, args))
            return "search result"

        try:
            text, name = await router.complete(
                [{"role": "user", "content": "đất hiếm Việt Nam?"}],
                [_search_tool()],
                execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual((text, name), ("Kết quả đã được tổng hợp.", "groq"))
        self.assertEqual(
            executed,
            [
                (
                    "web_search",
                    {"query": "Việt Nam công ty khai thác xuất khẩu đất hiếm 2024 2025"},
                )
            ],
        )
        self.assertEqual(len(requests), 2)

    async def test_tool_use_failed_falls_back_without_plain_retry(self) -> None:
        first_requests: list[dict] = []
        second_requests: list[dict] = []

        def first_respond(request: httpx.Request) -> httpx.Response:
            first_requests.append(json.loads(request.content))
            return httpx.Response(
                400,
                json={
                    "error": {
                        "code": "tool_use_failed",
                        "message": "Failed to call a function",
                        "failed_generation": "<tool_call>web_search</tool_call>",
                    }
                },
                request=request,
            )

        def second_respond(request: httpx.Request) -> httpx.Response:
            second_requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "fallback ok"}}]},
                request=request,
            )

        first = OpenAICompatProvider(
            "groq", "https://first.example/v1", "fake", "openai/gpt-oss-120b"
        )
        second = OpenAICompatProvider(
            "gemini", "https://second.example/v1", "fake", "gemini-3.8-flash"
        )
        await first.aclose()
        await second.aclose()
        first._client = httpx.AsyncClient(
            base_url="https://first.example/v1", transport=httpx.MockTransport(first_respond)
        )
        second._client = httpx.AsyncClient(
            base_url="https://second.example/v1", transport=httpx.MockTransport(second_respond)
        )
        router = AIProviderRouter([first, second], max_tool_rounds=2)

        async def execute(_name: str, _args: dict) -> str:
            return "unused"

        try:
            text, name = await router.complete(
                [{"role": "user", "content": "latest?"}],
                [_search_tool()],
                execute,
            )
        finally:
            await first.aclose()
            await second.aclose()

        self.assertEqual((text, name), ("fallback ok", "gemini"))
        self.assertEqual(len(first_requests), 1)
        self.assertIn("tools", second_requests[0])


def _search_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "parameters": {"type": "object", "properties": {}},
        },
    }


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


if __name__ == "__main__":
    unittest.main()
