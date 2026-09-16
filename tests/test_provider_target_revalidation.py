"""Deterministic concurrency regression for target revalidation before network calls."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.base import ChatResponse, ProviderError, ToolCall
from app.ai.router import AIProviderRouter
from tests.provider_fakes import ScriptedProvider, fetch_url_tool


class _ToolThenConcurrentRateLimitProvider(ScriptedProvider):
    """A starts a tool round; B rate-limits the same target before A continues."""

    def __init__(self) -> None:
        super().__init__("xkiro", [])
        self.model = "model-a"
        self.credential_id = "cred-1"
        self.target_id = "xkiro:text:m1:c1"
        self.call_count = 0

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        self.calls.append((messages, tools))
        self.call_count += 1
        if self.call_count == 1:
            return ChatResponse(
                tool_calls=[
                    ToolCall(
                        id="call_fetch",
                        name="fetch_url",
                        arguments={"url": "https://example.com"},
                    )
                ]
            )
        if self.call_count == 2:
            raise ProviderError(
                "rate limited by concurrent request",
                status_code=429,
                retry_after=120.0,
                transient=True,
            )
        return ChatResponse(content="stale-target-reused")


class _ToolThenConcurrentChainnodeAuthFailureProvider(ScriptedProvider):
    """A waits in a tool; B disables credential #1 before A can continue."""

    def __init__(self) -> None:
        super().__init__("chainnode", [])
        self.model = "model-a"
        self.credential_id = "cred-1"
        self.target_id = "chainnode:text:m1:c1"
        self.call_count = 0

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        self.calls.append((messages, tools))
        self.call_count += 1
        if self.call_count == 1:
            return ChatResponse(
                tool_calls=[
                    ToolCall(
                        id="call_fetch",
                        name="fetch_url",
                        arguments={"url": "https://example.com"},
                    )
                ]
            )
        if self.call_count == 2:
            raise ProviderError(
                "credential rejected by concurrent request",
                status_code=401,
                transient=False,
            )
        return ChatResponse(content="stale-chainnode-credential-reused")


def _target_two() -> ScriptedProvider:
    provider = ScriptedProvider(
        "xkiro",
        [ChatResponse(content="request-b"), ChatResponse(content="request-a")],
    )
    provider.model = "model-a"
    provider.credential_id = "cred-2"
    provider.target_id = "xkiro:text:m1:c2"
    return provider


def _chainnode_target_two() -> ScriptedProvider:
    provider = ScriptedProvider(
        "chainnode",
        [ChatResponse(content="request-b"), ChatResponse(content="request-a")],
    )
    provider.model = "model-a"
    provider.credential_id = "cred-2"
    provider.target_id = "chainnode:text:m1:c2"
    return provider


class ProviderTargetRevalidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_continuation_revalidates_target_after_concurrent_cooldown(self) -> None:
        raced = _ToolThenConcurrentRateLimitProvider()
        sibling = _target_two()
        router = AIProviderRouter(
            [raced, sibling],
            text_provider_order=("xkiro",),
            vision_provider_order=(),
        )
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            tool_started.set()
            await release_tool.wait()
            return "portable-evidence"

        request_a = asyncio.create_task(
            router.complete(
                [{"role": "user", "content": "request-a"}],
                [fetch_url_tool()],
                execute,
            )
        )
        await tool_started.wait()

        request_b = await router.complete(
            [{"role": "user", "content": "request-b"}],
            [fetch_url_tool()],
            execute,
        )
        self.assertEqual(request_b.content, "request-b")
        self.assertEqual(raced.call_count, 2)
        self.assertFalse(raced.health.available())

        release_tool.set()
        request_a_result = await request_a

        self.assertEqual(request_a_result.content, "request-a")
        self.assertEqual(
            raced.call_count,
            2,
            "request A must not send a stale continuation to a target cooled down by B",
        )
        self.assertEqual(len(sibling.calls), 2)
        self.assertEqual(tool_invocations, 1)

    async def test_chainnode_tool_continuation_revalidates_credential_after_concurrent_401(
        self,
    ) -> None:
        raced = _ToolThenConcurrentChainnodeAuthFailureProvider()
        sibling = _chainnode_target_two()
        router = AIProviderRouter(
            [raced, sibling],
            text_provider_order=("chainnode",),
            vision_provider_order=(),
        )
        tool_started = asyncio.Event()
        release_tool = asyncio.Event()
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            tool_started.set()
            await release_tool.wait()
            return "portable-evidence"

        request_a = asyncio.create_task(
            router.complete(
                [{"role": "user", "content": "request-a"}],
                [fetch_url_tool()],
                execute,
            )
        )
        await tool_started.wait()

        request_b = await router.complete(
            [{"role": "user", "content": "request-b"}],
            [fetch_url_tool()],
            execute,
        )
        self.assertEqual(request_b.content, "request-b")
        self.assertEqual(raced.call_count, 2)

        release_tool.set()
        request_a_result = await request_a

        self.assertEqual(request_a_result.content, "request-a")
        self.assertEqual(
            raced.call_count,
            2,
            "request A must not call credential #1 after B disabled that credential scope",
        )
        self.assertEqual(len(sibling.calls), 2)
        self.assertEqual(tool_invocations, 1)


if __name__ == "__main__":
    unittest.main()
