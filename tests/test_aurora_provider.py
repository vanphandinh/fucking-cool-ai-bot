from __future__ import annotations

import asyncio
import json
import unittest

import httpx

from app.ai.base import ProviderError
from app.ai.registry import PROVIDER_FACTORIES
from app.ai.router import AIProviderRouter, CompletionResult, build_provider_router
from app.config import Settings


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


async def _close_router(router: AIProviderRouter) -> None:
    for provider in router.providers:
        await provider.aclose()


class AuroraWireContractTests(unittest.IsolatedAsyncioTestCase):
    async def _build_aurora_router(self) -> AIProviderRouter:
        self.assertIn("aurora", PROVIDER_FACTORIES)
        router = build_provider_router(
            Settings(
                _env_file=None,
                aurora_api_key="internal-secret",
                text_provider_order="aurora",
                vision_provider_order="",
                vision_enabled=False,
            )
        )
        self.assertEqual(router.configured_provider_names(False), ("aurora",))
        return router

    async def test_chat_uses_service_bearer_key_and_openai_payload(self) -> None:
        router = await self._build_aurora_router()
        provider = router.providers[0]
        seen: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                request=request,
            )

        await provider.aclose()
        provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
            base_url="http://aurora:8080/v1/",
            headers={"Authorization": "Bearer internal-secret"},
            transport=httpx.MockTransport(respond),
        )
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

    async def test_full_tool_round_trip_returns_completion_result(self) -> None:
        router = await self._build_aurora_router()
        provider = router.providers[0]
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

        await provider.aclose()
        provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
            base_url="http://aurora:8080/v1/",
            headers={"Authorization": "Bearer internal-secret"},
            transport=httpx.MockTransport(respond),
        )
        try:
            result = await router.complete(
                [{"role": "user", "content": "find aurora"}],
                [_search_tool()],
                tool_executor,
            )
        finally:
            await provider.aclose()

        self.assertIsInstance(result, CompletionResult)
        self.assertEqual(result.content, "final from aurora")
        self.assertEqual(result.provider, "aurora")
        self.assertEqual(result.fallbacks, ())
        self.assertEqual(executed, [("web_search", {"query": "aurora"})])
        self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
        self.assertEqual(requests[1]["messages"][-1]["tool_call_id"], "call_aurora")

    async def test_unknown_raw_tool_markup_is_rejected(self) -> None:
        router = await self._build_aurora_router()
        provider = router.providers[0]

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": (
                                    "<tool_call>unknown_tool<arg_key>x</arg_key>"
                                    "<arg_value>1</arg_value></tool_call>"
                                ),
                            }
                        }
                    ]
                },
                request=request,
            )

        await provider.aclose()
        provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
            base_url="http://aurora:8080/v1/",
            headers={"Authorization": "Bearer internal-secret"},
            transport=httpx.MockTransport(respond),
        )
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
