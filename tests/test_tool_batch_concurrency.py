from __future__ import annotations

import asyncio
import unittest

from app.ai.base import ChatResponse, ToolCall
from app.ai.router import AIProviderRouter, CompletionResult, _execute_tool_batch
from app.core.job_control import JobStopped


class _BatchProvider:
    name = "batch"
    supports_tools = True

    def __init__(self) -> None:
        self.calls = 0
        self.synthesis_messages: list[dict] = []

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResponse:
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                tool_calls=[
                    ToolCall("call_a", "fetch_url", {"url": "a"}),
                    ToolCall("call_b", "fetch_url", {"url": "b"}),
                    ToolCall("call_c", "fetch_url", {"url": "c"}),
                ]
            )
        self.synthesis_messages = messages
        return ChatResponse(content="done")


class ToolBatchConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_batch_runs_two_at_a_time_and_preserves_model_order_in_fresh_evidence(
        self,
    ) -> None:
        provider = _BatchProvider()
        router = AIProviderRouter([provider], max_tool_rounds=1)
        active = 0
        max_active = 0
        completion_order: list[str] = []
        delays = {"a": 0.05, "b": 0.01, "c": 0.005}

        async def execute(_name: str, args: dict) -> str:
            nonlocal active, max_active
            label = str(args["url"])
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(delays[label])
                completion_order.append(label)
                return f"result-{label}"
            finally:
                active -= 1

        result = await router.complete(
            [{"role": "user", "content": "read three URLs"}],
            [{"type": "function", "function": {"name": "fetch_url"}}],
            execute,
        )

        self.assertEqual(result, CompletionResult("done", "batch", ()))
        self.assertEqual(max_active, 2)
        self.assertEqual(completion_order, ["b", "c", "a"])
        self.assertFalse(
            any(message.get("role") == "tool" for message in provider.synthesis_messages)
        )
        self.assertFalse(
            any(message.get("tool_calls") for message in provider.synthesis_messages)
        )
        evidence = str(provider.synthesis_messages[-1].get("content") or "")
        positions = [evidence.index(f"result-{label}") for label in ("a", "b", "c")]
        self.assertEqual(positions, sorted(positions))

    async def test_cancelled_tool_batch_cancels_and_joins_sibling_tools(self) -> None:
        sibling_started = asyncio.Event()
        sibling_cancelled = asyncio.Event()
        sibling_finished = asyncio.Event()

        async def execute(name: str, _args: dict) -> str:
            if name == "stop":
                await sibling_started.wait()
                raise JobStopped()
            sibling_started.set()
            try:
                await asyncio.sleep(1)
                sibling_finished.set()
                return "late-result"
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise

        calls = [
            ToolCall("call_stop", "stop", {}),
            ToolCall("call_sibling", "sibling", {}),
        ]
        with self.assertRaises(JobStopped):
            await _execute_tool_batch(calls, execute)

        # Returning from the batch on cancellation must mean no owned child is still
        # running. Otherwise a Stop can leave a tool mutating state after job teardown.
        await asyncio.sleep(0)
        self.assertTrue(sibling_cancelled.is_set())
        self.assertFalse(sibling_finished.is_set())


if __name__ == "__main__":
    unittest.main()
