from __future__ import annotations

import asyncio
import unittest

from app.ai.capabilities import ProviderCapabilities
from app.ai.contracts import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    TextPart,
    ToolCallPart as ToolCall,
    ToolDefinition,
    ToolResultPart,
)
from app.ai.health import ProviderHealth
from app.ai.recovery import RecoveryPolicy
from app.ai.router import AIProviderRouter, CompletionResult, _execute_tool_batch
from app.ai.target import ProviderTargetIdentity, TargetSpec
from app.core.job_control import JobStopped


class _BatchProvider:
    name = "batch"
    supports_tools = True

    def __init__(self) -> None:
        self.spec = TargetSpec(
            identity=ProviderTargetIdentity(
                family="batch",
                route="text",
                model="batch-model",
                credential_id="cred-1",
                target_id="batch:text:m1:c1",
            ),
            driver="fake",
            capabilities=ProviderCapabilities(route="text"),
            base_url="https://example.invalid/v1",
            request_timeout_sec=60.0,
            recovery_policy=RecoveryPolicy(),
        )
        self.capabilities = self.spec.capabilities
        self.health = ProviderHealth()
        self.calls = 0
        self.synthesis_request: ChatRequest | None = None

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                tool_calls=(
                    ToolCall("call_a", "fetch_url", {"url": "a"}),
                    ToolCall("call_b", "fetch_url", {"url": "b"}),
                    ToolCall("call_c", "fetch_url", {"url": "c"}),
                )
            )
        self.synthesis_request = request
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
            ChatRequest(
                messages=(
                    ChatMessage("user", (TextPart("read three URLs"),)),
                ),
                tools=(
                    ToolDefinition(
                        name="fetch_url",
                        description="Fetch URL",
                        parameters={"type": "object", "properties": {}},
                    ),
                ),
            ),
            execute,
        )

        self.assertEqual(result, CompletionResult("done", "batch", ()))
        self.assertEqual(max_active, 2)
        self.assertEqual(completion_order, ["b", "c", "a"])
        self.assertIsNotNone(provider.synthesis_request)
        assert provider.synthesis_request is not None
        messages = provider.synthesis_request.messages
        self.assertFalse(
            any(
                isinstance(part, ToolResultPart)
                for message in messages
                for part in message.parts
            )
        )
        self.assertFalse(
            any(
                isinstance(part, ToolCall)
                for message in messages
                for part in message.parts
            )
        )
        evidence = "".join(
            part.text
            for part in messages[-1].parts
            if isinstance(part, TextPart)
        )
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

        calls = (
            ToolCall("call_stop", "stop", {}),
            ToolCall("call_sibling", "sibling", {}),
        )
        with self.assertRaises(JobStopped):
            await _execute_tool_batch(calls, execute)

        await asyncio.sleep(0)
        self.assertTrue(sibling_cancelled.is_set())
        self.assertFalse(sibling_finished.is_set())


if __name__ == "__main__":
    unittest.main()
