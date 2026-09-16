"""Regressions for transport retry during provider-local recovery passes."""

from __future__ import annotations

import unittest

from app.ai.base import ChatResponse, ProviderError
from app.ai.router import AIProviderRouter
from tests.provider_fakes import ScriptedProvider, noop_tool


class ProviderLocalRecoveryRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_without_tools_does_not_block_followup_transport_recovery(
        self,
    ) -> None:
        protocol_error = ProviderError(
            "retry plain",
            retry_without_tools=True,
            transient=False,
        )
        timeout_error = ProviderError("connect timeout", transient=True)
        timeout_error.transport_kind = "connect_timeout"
        provider = ScriptedProvider(
            "xkiro",
            [
                protocol_error,
                timeout_error,
                ChatResponse(content="recovered"),
            ],
        )
        router = AIProviderRouter([provider], text_provider_order=("xkiro",))

        result = await router.complete(
            [{"role": "user", "content": "hello"}],
            [{"type": "function", "function": {"name": "noop"}}],
            noop_tool,
        )

        self.assertEqual(result.content, "recovered")
        self.assertEqual(len(provider.calls), 3)


if __name__ == "__main__":
    unittest.main()
