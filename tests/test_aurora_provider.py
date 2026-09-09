from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
import unittest

import httpx

import app.ai.aurora as aurora
from app.ai.base import ProviderError
from app.ai.router import AIProviderRouter
from app.config import Settings


class AuroraProviderFactoryTests(unittest.TestCase):
    def test_default_settings_match_private_sidecar(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.aurora_base_url, "http://aurora:8080/v1")
        self.assertEqual(settings.aurora_api_key, "")
        self.assertEqual(settings.aurora_model, "auto")
        self.assertEqual(settings.aurora_request_timeout_sec, 90.0)

    def test_factory_is_text_only_and_uses_temporary_auth_policy(self) -> None:
        settings = SimpleNamespace(
            aurora_base_url="http://aurora:8080/v1",
            aurora_api_key="internal-secret",
            aurora_model="auto",
            aurora_request_timeout_sec=90.0,
        )
        provider = aurora.make_aurora_provider(settings)
        try:
            self.assertEqual(provider.name, "aurora")
            self.assertEqual(provider.model, "auto")
            self.assertEqual(str(provider._client.base_url), "http://aurora:8080/v1/")
            self.assertEqual(provider.capabilities.route, "text")
            self.assertFalse(provider.capabilities.supports_vision)
            self.assertEqual(provider.capabilities.max_images, 0)
            self.assertEqual(provider.health.auth_failure_cooldown_sec, 60.0)
        finally:
            asyncio.run(provider.aclose())


def _search_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }


async def _mocked_provider(handler) -> object:
    settings = SimpleNamespace(
        aurora_base_url="http://aurora:8080/v1",
        aurora_api_key="internal-secret",
        aurora_model="auto",
        aurora_request_timeout_sec=90.0,
    )
    provider = aurora.make_aurora_provider(settings)
    await provider.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://aurora:8080/v1/",
        headers={"Authorization": "Bearer internal-secret"},
        transport=httpx.MockTransport(handler),
    )
    return provider


class AuroraWireContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_uses_service_bearer_key_and_openai_payload(self) -> None:
        seen: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                request=request,
            )

        provider = await _mocked_provider(respond)
        try:
            result = await provider.chat(
                [{"role": "user", "content": "hello"}],
                [_search_tool()],
            )
        finally:
            await provider.aclose()

        self.assertEqual(result.content, "ok")
        self.assertEqual(seen[0].url.path, "/v1/chat/completions")
        self.assertEqual(seen[0].headers["authorization"], "Bearer internal-secret")
        payload = json.loads(seen[0].content)
        self.assertEqual(payload["model"], "auto")
        self.assertEqual(set(payload), {"model", "messages", "tools"})

    async def test_openai_tool_call_is_parsed(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_aurora",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query":"aurora"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                request=request,
            )

        provider = await _mocked_provider(respond)
        try:
            result = await provider.chat(
                [{"role": "user", "content": "search"}],
                [_search_tool()],
            )
        finally:
            await provider.aclose()

        self.assertEqual(result.tool_calls[0].id, "call_aurora")
        self.assertEqual(result.tool_calls[0].name, "web_search")
        self.assertEqual(result.tool_calls[0].arguments, {"query": "aurora"})

    async def test_full_tool_round_trip_stays_in_existing_router_loop(self) -> None:
        requests: list[dict] = []
        executed: list[tuple[str, dict]] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                body = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_aurora",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query":"aurora"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            else:
                body = {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "final from aurora",
                            }
                        }
                    ]
                }
            return httpx.Response(200, json=body, request=request)

        async def tool_executor(name: str, arguments: dict) -> str:
            executed.append((name, arguments))
            return "search result"

        provider = await _mocked_provider(respond)
        router = AIProviderRouter([provider], max_tool_rounds=2)
        try:
            answer, provider_name = await router.complete(
                [{"role": "user", "content": "find aurora"}],
                [_search_tool()],
                tool_executor,
            )
        finally:
            await provider.aclose()

        self.assertEqual(answer, "final from aurora")
        self.assertEqual(provider_name, "aurora")
        self.assertEqual(executed, [("web_search", {"query": "aurora"})])
        self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
        self.assertEqual(requests[1]["messages"][-1]["tool_call_id"], "call_aurora")

    async def test_unknown_raw_tool_markup_is_rejected(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "<tool_call>unknown_tool"
                                    "<arg_key>x</arg_key><arg_value>1</arg_value>"
                                    "</tool_call>"
                                ),
                            }
                        }
                    ]
                },
                request=request,
            )

        provider = await _mocked_provider(respond)
        try:
            with self.assertRaises(ProviderError):
                await provider.chat(
                    [{"role": "user", "content": "test"}],
                    [_search_tool()],
                )
        finally:
            await provider.aclose()


if __name__ == "__main__":
    unittest.main()
