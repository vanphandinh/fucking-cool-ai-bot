"""Concurrency regressions for request-local AI router state."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.base import ChatResponse, ProviderError
from app.ai.capabilities import ProviderCapabilities
from app.ai.health import ProviderHealth
from app.ai.router import AIProviderRouter


class _FirstProvider:
    name = "first"
    supports_tools = True
    capabilities = ProviderCapabilities()

    def __init__(self) -> None:
        self.health = ProviderHealth()

    async def chat(self, messages, tools=None):
        marker = str(messages[-1].get("content") or "")
        if marker == "fallback":
            raise ProviderError("first failed")
        return ChatResponse(content="direct")


class _SecondProvider:
    name = "second"
    supports_tools = True
    capabilities = ProviderCapabilities()

    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self.health = ProviderHealth()
        self.entered = entered
        self.release = release

    async def chat(self, messages, tools=None):
        self.entered.set()
        await self.release.wait()
        return ChatResponse(content="fallback-result")


class RouterConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_count_is_request_local(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        router = AIProviderRouter([_FirstProvider(), _SecondProvider(entered, release)])

        async def tool_executor(name: str, args: dict) -> str:
            return "unused"

        async def run(marker: str) -> tuple[str, int]:
            text, _provider = await router.complete(
                [{"role": "user", "content": marker}],
                None,
                tool_executor,
            )
            return text, router.last_fallbacks

        fallback_task = asyncio.create_task(run("fallback"))
        await entered.wait()
        direct_result = await run("direct")
        release.set()
        fallback_result = await fallback_task

        self.assertEqual(direct_result, ("direct", 0))
        self.assertEqual(fallback_result, ("fallback-result", 1))


if __name__ == "__main__":
    unittest.main()
