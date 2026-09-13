"""Request-scoped same-provider retry regressions for AI transport failures."""

from __future__ import annotations

import unittest

from app.ai.base import AllProvidersFailed, ChatResponse, ProviderError, ToolCall
from app.ai.router import AIProviderRouter, CompletionResult
from tests.provider_fakes import ScriptedProvider, fetch_url_tool, noop_tool


class ProviderTransportRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_error_retries_same_provider_once(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("connect_error"), ChatResponse(content="recovered")],
        )
        bai = ScriptedProvider("bai", [ChatResponse(content="unused")])
        router = _router(chainnode, bai)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("recovered", "chainnode", ()))
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(bai.calls), 0)
        self.assertEqual(chainnode.health.consecutive_transient_failures, 0)

    async def test_connect_timeout_retries_same_provider_then_recovers(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("connect_timeout"), ChatResponse(content="recovered")],
        )
        bai = ScriptedProvider("bai", [ChatResponse(content="unused")])
        router = _router(chainnode, bai)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("recovered", "chainnode", ()))
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(bai.calls), 0)
        self.assertEqual(chainnode.health.consecutive_transient_failures, 0)

    async def test_connect_timeout_exhausts_retry_then_falls_back_once(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("connect_timeout"), _transport_error("connect_timeout")],
        )
        bai = ScriptedProvider("bai", [ChatResponse(content="fallback")])
        router = _router(chainnode, bai)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("fallback", "bai", ("bai",)))
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(bai.calls), 1)
        self.assertEqual(chainnode.health.consecutive_transient_failures, 1)

    async def test_read_timeout_prefers_healthy_ordered_fallback(self) -> None:
        chainnode = ScriptedProvider("chainnode", [_transport_error("read_timeout")])
        bai = ScriptedProvider("bai", [ChatResponse(content="fallback")])
        router = _router(chainnode, bai)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("fallback", "bai", ("bai",)))
        self.assertEqual(len(chainnode.calls), 1)
        self.assertEqual(len(bai.calls), 1)

    async def test_read_timeout_wraps_to_previous_provider_and_recovers(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [
                _transport_error("read_timeout"),
                ChatResponse(content="recovered on wrap"),
            ],
        )
        bai = ScriptedProvider("bai", [_transport_error("read_timeout")])
        router = _router(chainnode, bai)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(
            result,
            CompletionResult("recovered on wrap", "chainnode", ("bai", "chainnode")),
        )
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(bai.calls), 1)

    async def test_cyclic_read_timeouts_stop_after_bounded_failures(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("read_timeout"), _transport_error("read_timeout")],
        )
        bai = ScriptedProvider(
            "bai",
            [_transport_error("read_timeout"), _transport_error("read_timeout")],
        )
        router = _router(chainnode, bai)

        with self.assertRaises(AllProvidersFailed):
            await router.complete(_messages(), None, noop_tool)

        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(bai.calls), 2)

    async def test_read_timeout_rechecks_fallback_health_at_failure_time(self) -> None:
        bai = ScriptedProvider("bai", [ChatResponse(content="must not be called")])
        chainnode = _FallbackDisablingProvider(
            "chainnode",
            [_transport_error("read_timeout"), ChatResponse(content="recovered")],
            fallback=bai,
        )
        router = _router(chainnode, bai)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("recovered", "chainnode", ()))
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(bai.calls, [])
        self.assertEqual(chainnode.health.consecutive_transient_failures, 0)

    async def test_read_timeout_retries_final_healthy_provider_once(self) -> None:
        bai = ScriptedProvider(
            "bai",
            [_transport_error("read_timeout"), ChatResponse(content="recovered")],
        )
        router = AIProviderRouter([bai], text_provider_order=("bai",))

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("recovered", "bai", ()))
        self.assertEqual(len(bai.calls), 2)
        self.assertEqual(bai.health.consecutive_transient_failures, 0)

    async def test_write_pool_http_and_non_transient_errors_do_not_retry(self) -> None:
        cases = (
            ("write_timeout", _transport_error("write_timeout")),
            ("pool_timeout", _transport_error("pool_timeout")),
            (
                "http_429",
                ProviderError("rate limited", status_code=429, retry_after=5.0, transient=True),
            ),
            ("http_503", ProviderError("unavailable", status_code=503, transient=True)),
            ("non_transient", ProviderError("bad response", transient=False)),
        )
        for label, error in cases:
            with self.subTest(label=label):
                first = ScriptedProvider("first", [error])
                second = ScriptedProvider("second", [ChatResponse(content="fallback")])
                router = AIProviderRouter(
                    [first, second],
                    text_provider_order=("first", "second"),
                )

                result = await router.complete(_messages(), None, noop_tool)

                self.assertEqual(result.provider, "second")
                self.assertEqual(result.fallbacks, ("second",))
                self.assertEqual(len(first.calls), 1)
                self.assertEqual(len(second.calls), 1)

    async def test_read_timeout_retries_exact_model_continuation_without_rerunning_tool(
        self,
    ) -> None:
        provider = ScriptedProvider(
            "bai",
            [
                _tool_call(),
                _transport_error("read_timeout"),
                ChatResponse(content="final answer"),
            ],
        )
        router = AIProviderRouter([provider], text_provider_order=("bai",))
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            return "TOOL-EVIDENCE"

        result = await router.complete(
            _messages(),
            [fetch_url_tool()],
            execute,
        )

        self.assertEqual(result, CompletionResult("final answer", "bai", ()))
        self.assertEqual(tool_invocations, 1)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(provider.calls[1], provider.calls[2])
        self.assertIn("TOOL-EVIDENCE", repr(provider.calls[1][0]))

    async def test_one_retry_budget_is_shared_across_tool_rounds(self) -> None:
        provider = ScriptedProvider(
            "bai",
            [
                _transport_error("connect_timeout"),
                _tool_call(),
                _transport_error("read_timeout"),
                ChatResponse(content="must not be reached"),
            ],
        )
        router = AIProviderRouter([provider], text_provider_order=("bai",))
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            return "TOOL-EVIDENCE"

        with self.assertRaises(AllProvidersFailed):
            await router.complete(
                _messages(),
                [fetch_url_tool()],
                execute,
            )

        self.assertEqual(tool_invocations, 1)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(provider.health.consecutive_transient_failures, 1)

    async def test_consumed_retry_budget_falls_back_after_later_read_timeout(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [
                _transport_error("connect_timeout"),
                _tool_call(),
                _transport_error("read_timeout"),
            ],
        )
        bai = ScriptedProvider("bai", [ChatResponse(content="fallback answer")])
        router = _router(chainnode, bai)
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            return "PORTABLE-EVIDENCE"

        result = await router.complete(
            _messages(),
            [fetch_url_tool()],
            execute,
        )

        self.assertEqual(result, CompletionResult("fallback answer", "bai", ("bai",)))
        self.assertEqual(tool_invocations, 1)
        self.assertEqual(len(chainnode.calls), 3)
        self.assertEqual(len(bai.calls), 1)
        self.assertIn("PORTABLE-EVIDENCE", repr(bai.calls[0][0]))


class _FallbackDisablingProvider(ScriptedProvider):
    def __init__(
        self,
        name: str,
        script: list[ChatResponse | Exception],
        *,
        fallback: ScriptedProvider,
    ) -> None:
        super().__init__(name, script)
        self._fallback = fallback

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        self._fallback.health.disabled = True
        return await super().chat(messages, tools)


def _transport_error(kind: str) -> ProviderError:
    error = ProviderError(f"transport failure: {kind}", transient=True)
    error.transport_kind = kind
    return error


def _tool_call() -> ChatResponse:
    return ChatResponse(
        tool_calls=[
            ToolCall(
                id="call_fetch",
                name="fetch_url",
                arguments={"url": "https://example.com"},
            )
        ]
    )


def _router(chainnode: ScriptedProvider, bai: ScriptedProvider) -> AIProviderRouter:
    return AIProviderRouter(
        [chainnode, bai],
        text_provider_order=("chainnode", "bai"),
    )


def _messages() -> list[dict]:
    return [{"role": "user", "content": "hello"}]


if __name__ == "__main__":
    unittest.main()
