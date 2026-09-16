"""Request-scoped bounded cyclic retry regressions for AI transport failures."""

from __future__ import annotations

import unittest

from app.ai.base import AllProvidersFailed, ChatResponse, ProviderError, ToolCall
from app.ai.retry import ProviderRetryPolicy
from app.ai.router import AIProviderRouter, CompletionResult
from tests.provider_fakes import ScriptedProvider, fetch_url_tool, noop_tool


class ProviderTransportRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_error_retries_same_provider_once(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("connect_error"), ChatResponse(content="recovered")],
        )
        xkiro = ScriptedProvider("xkiro", [ChatResponse(content="unused")])
        router = _router(chainnode, xkiro)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("recovered", "chainnode", ()))
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(xkiro.calls), 0)
        self.assertEqual(chainnode.health.consecutive_transient_failures, 0)

    async def test_connect_timeout_retries_same_provider_then_recovers(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("connect_timeout"), ChatResponse(content="recovered")],
        )
        xkiro = ScriptedProvider("xkiro", [ChatResponse(content="unused")])
        router = _router(chainnode, xkiro)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("recovered", "chainnode", ()))
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(xkiro.calls), 0)
        self.assertEqual(chainnode.health.consecutive_transient_failures, 0)

    async def test_connect_timeout_exhausts_retry_then_falls_back_once(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("connect_timeout"), _transport_error("connect_timeout")],
        )
        xkiro = ScriptedProvider("xkiro", [ChatResponse(content="fallback")])
        router = _router(chainnode, xkiro)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("fallback", "xkiro", ("xkiro",)))
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(xkiro.calls), 1)
        self.assertEqual(chainnode.health.consecutive_transient_failures, 1)

    async def test_read_timeout_prefers_healthy_ordered_fallback(self) -> None:
        chainnode = ScriptedProvider("chainnode", [_transport_error("read_timeout")])
        xkiro = ScriptedProvider("xkiro", [ChatResponse(content="fallback")])
        router = _router(chainnode, xkiro)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("fallback", "xkiro", ("xkiro",)))
        self.assertEqual(len(chainnode.calls), 1)
        self.assertEqual(len(xkiro.calls), 1)

    async def test_read_timeout_wraps_to_previous_provider_and_recovers(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [
                _transport_error("read_timeout"),
                ChatResponse(content="recovered on wrap"),
            ],
        )
        xkiro = ScriptedProvider("xkiro", [_transport_error("read_timeout")])
        router = _router(chainnode, xkiro)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(
            result,
            CompletionResult("recovered on wrap", "chainnode", ("xkiro", "chainnode")),
        )
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(xkiro.calls), 1)

    async def test_cyclic_read_timeouts_stop_after_bounded_failures(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("read_timeout"), _transport_error("read_timeout")],
        )
        xkiro = ScriptedProvider(
            "xkiro",
            [_transport_error("read_timeout"), _transport_error("read_timeout")],
        )
        router = _router(chainnode, xkiro)

        with self.assertRaises(AllProvidersFailed):
            await router.complete(_messages(), None, noop_tool)

        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(xkiro.calls), 2)
        self.assertEqual(chainnode.health.consecutive_transient_failures, 1)
        self.assertEqual(xkiro.health.consecutive_transient_failures, 1)

    async def test_read_timeout_rechecks_fallback_health_at_failure_time(self) -> None:
        xkiro = ScriptedProvider("xkiro", [ChatResponse(content="must not be called")])
        chainnode = _FallbackDisablingProvider(
            "chainnode",
            [_transport_error("read_timeout"), ChatResponse(content="recovered")],
            fallback=xkiro,
        )
        router = _router(chainnode, xkiro)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("recovered", "chainnode", ()))
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(xkiro.calls, [])
        self.assertEqual(chainnode.health.consecutive_transient_failures, 0)

    async def test_read_timeout_rechecks_wrapped_alternative_health_at_failure_time(self) -> None:
        chainnode = ScriptedProvider("chainnode", [_transport_error("read_timeout")])
        xkiro = _FallbackDisablingProvider(
            "xkiro",
            [_transport_error("read_timeout"), ChatResponse(content="xkiro recovered")],
            fallback=chainnode,
        )
        router = _router(chainnode, xkiro)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("xkiro recovered", "xkiro", ("xkiro",)))
        self.assertEqual(len(chainnode.calls), 1)
        self.assertEqual(len(xkiro.calls), 2)

    async def test_existing_shared_health_failure_does_not_prevent_wrap_recovery(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("read_timeout"), ChatResponse(content="recovered")],
        )
        xkiro = ScriptedProvider("xkiro", [_transport_error("read_timeout")])
        chainnode.health.consecutive_transient_failures = 1
        router = _router(chainnode, xkiro)

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result.provider, "chainnode")
        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(chainnode.health.consecutive_transient_failures, 0)

    async def test_read_timeout_retries_final_healthy_provider_once(self) -> None:
        xkiro = ScriptedProvider(
            "xkiro",
            [_transport_error("read_timeout"), ChatResponse(content="recovered")],
        )
        router = AIProviderRouter([xkiro], text_provider_order=("xkiro",))

        result = await router.complete(_messages(), None, noop_tool)

        self.assertEqual(result, CompletionResult("recovered", "xkiro", ()))
        self.assertEqual(len(xkiro.calls), 2)
        self.assertEqual(xkiro.health.consecutive_transient_failures, 0)

    async def test_non_retryable_failures_do_not_gain_cyclic_revisit(self) -> None:
        cases = (
            ("write_timeout", lambda: _transport_error("write_timeout")),
            ("pool_timeout", lambda: _transport_error("pool_timeout")),
            (
                "http_429",
                lambda: ProviderError(
                    "rate limited", status_code=429, retry_after=5.0, transient=True
                ),
            ),
            ("http_503", lambda: ProviderError("unavailable", status_code=503, transient=True)),
            ("non_transient", lambda: ProviderError("bad response", transient=False)),
        )
        for label, error_factory in cases:
            with self.subTest(label=label):
                first = ScriptedProvider("first", [error_factory()])
                second = ScriptedProvider("second", [error_factory()])
                router = AIProviderRouter(
                    [first, second],
                    text_provider_order=("first", "second"),
                )

                with self.assertRaises(AllProvidersFailed):
                    await router.complete(_messages(), None, noop_tool)

                self.assertEqual(len(first.calls), 1)
                self.assertEqual(len(second.calls), 1)

    async def test_read_timeout_retries_exact_model_continuation_without_rerunning_tool(
        self,
    ) -> None:
        provider = ScriptedProvider(
            "xkiro",
            [
                _tool_call(),
                _transport_error("read_timeout"),
                ChatResponse(content="final answer"),
            ],
        )
        router = AIProviderRouter([provider], text_provider_order=("xkiro",))
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            return "TOOL-EVIDENCE"

        result = await router.complete(_messages(), [fetch_url_tool()], execute)

        self.assertEqual(result, CompletionResult("final answer", "xkiro", ()))
        self.assertEqual(tool_invocations, 1)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(provider.calls[1], provider.calls[2])
        self.assertIn("TOOL-EVIDENCE", repr(provider.calls[1][0]))

    async def test_completed_tool_is_not_rerun_across_rotation_and_wrap(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [
                _tool_call(),
                _transport_error("read_timeout"),
                ChatResponse(content="final after wrap"),
            ],
        )
        xkiro = ScriptedProvider("xkiro", [_transport_error("read_timeout")])
        router = _router(chainnode, xkiro)
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            return "PORTABLE-EVIDENCE"

        result = await router.complete(_messages(), [fetch_url_tool()], execute)

        self.assertEqual(
            result,
            CompletionResult("final after wrap", "chainnode", ("xkiro", "chainnode")),
        )
        self.assertEqual(tool_invocations, 1)
        self.assertEqual(len(chainnode.calls), 3)
        self.assertEqual(len(xkiro.calls), 1)
        self.assertIn("PORTABLE-EVIDENCE", repr(xkiro.calls[0][0]))
        self.assertIn("PORTABLE-EVIDENCE", repr(chainnode.calls[-1][0]))

    async def test_tool_chat_success_does_not_refund_same_provider_retry(self) -> None:
        provider = ScriptedProvider(
            "xkiro",
            [
                _transport_error("connect_timeout"),
                _tool_call(),
                _transport_error("read_timeout"),
                ChatResponse(content="must not be reached"),
            ],
        )
        router = AIProviderRouter([provider], text_provider_order=("xkiro",))
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            return "TOOL-EVIDENCE"

        with self.assertRaises(AllProvidersFailed):
            await router.complete(_messages(), [fetch_url_tool()], execute)

        self.assertEqual(tool_invocations, 1)
        self.assertEqual(len(provider.calls), 3)

    async def test_provider_cumulative_budget_survives_tool_chat_success(self) -> None:
        provider = ScriptedProvider(
            "xkiro",
            [
                _transport_error("connect_timeout"),
                _tool_call(),
                _transport_error("read_timeout"),
                ChatResponse(content="must not be reached"),
            ],
        )
        router = AIProviderRouter(
            [provider],
            text_provider_order=("xkiro",),
            retry_policy=ProviderRetryPolicy(
                max_consecutive_failures=2,
                max_failures_per_provider=2,
                max_failures_per_request=5,
            ),
        )
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            return "TOOL-EVIDENCE"

        with self.assertRaises(AllProvidersFailed):
            await router.complete(_messages(), [fetch_url_tool()], execute)

        self.assertEqual(tool_invocations, 1)
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(provider.health.consecutive_transient_failures, 1)

    async def test_request_cumulative_budget_bounds_all_providers(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [_transport_error("read_timeout"), _transport_error("read_timeout")],
        )
        xkiro = ScriptedProvider(
            "xkiro",
            [_transport_error("read_timeout"), ChatResponse(content="must not run")],
        )
        router = _router(
            chainnode,
            xkiro,
            retry_policy=ProviderRetryPolicy(
                max_consecutive_failures=3,
                max_failures_per_provider=4,
                max_failures_per_request=3,
            ),
        )

        with self.assertRaises(AllProvidersFailed):
            await router.complete(_messages(), None, noop_tool)

        self.assertEqual(len(chainnode.calls), 2)
        self.assertEqual(len(xkiro.calls), 1)

    async def test_healthy_alternative_drives_later_read_timeout_fallback(self) -> None:
        chainnode = ScriptedProvider(
            "chainnode",
            [
                _transport_error("connect_timeout"),
                _tool_call(),
                _transport_error("read_timeout"),
            ],
        )
        xkiro = ScriptedProvider("xkiro", [ChatResponse(content="fallback answer")])
        router = _router(chainnode, xkiro)
        tool_invocations = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal tool_invocations
            tool_invocations += 1
            return "PORTABLE-EVIDENCE"

        result = await router.complete(_messages(), [fetch_url_tool()], execute)

        self.assertEqual(result, CompletionResult("fallback answer", "xkiro", ("xkiro",)))
        self.assertEqual(tool_invocations, 1)
        self.assertEqual(len(chainnode.calls), 3)
        self.assertEqual(len(xkiro.calls), 1)
        self.assertIn("PORTABLE-EVIDENCE", repr(xkiro.calls[0][0]))


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


def _router(
    chainnode: ScriptedProvider,
    xkiro: ScriptedProvider,
    *,
    retry_policy: ProviderRetryPolicy | None = None,
) -> AIProviderRouter:
    return AIProviderRouter(
        [chainnode, xkiro],
        text_provider_order=("chainnode", "xkiro"),
        retry_policy=retry_policy,
    )


def _messages() -> list[dict]:
    return [{"role": "user", "content": "hello"}]


if __name__ == "__main__":
    unittest.main()
