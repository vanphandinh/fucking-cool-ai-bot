"""Fresh-audit regressions for PR #58."""

from __future__ import annotations

import unittest

from app.ai.base import ProviderError
from app.ai.contracts import ChatResponse, ToolCallPart, ToolResultPart
from app.ai.router import AIProviderRouter
from app.ai.runtime_config import ProviderRuntimeSettings
from app.config import Settings
from tests.provider_fakes import (
    ScriptedProvider,
    fetch_url_definition,
    noop_tool,
    text_request,
    tool_response,
)


class RuntimeSecretReprRegressionTests(unittest.TestCase):
    def test_provider_api_keys_are_absent_from_runtime_and_settings_repr(self) -> None:
        secret = "PR58-FRESH-AUDIT-SECRET"
        runtime = ProviderRuntimeSettings(api_keys=secret, text_models="model-a")
        settings = Settings(
            _env_file=None,
            ai_providers={
                "chainnode": {
                    "api_keys": secret,
                    "text_models": "model-a",
                }
            },
            text_provider_order="chainnode",
            vision_enabled=False,
        )

        self.assertNotIn(secret, repr(runtime))
        self.assertNotIn(secret, repr(settings))


class ToolPortabilityCapabilityRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_tools_fallback_receives_plain_evidence_not_structured_tool_history(
        self,
    ) -> None:
        primary = ScriptedProvider(
            "primary",
            [
                tool_response(1, "research"),
                ProviderError("primary failed", transient=False),
            ],
        )
        fallback = ScriptedProvider(
            "fallback",
            [ChatResponse(content="fallback synthesis")],
        )
        fallback.supports_tools = False
        router = AIProviderRouter(
            [primary, fallback],
            text_provider_order=("primary", "fallback"),
            max_tool_rounds=4,
        )
        executed = 0

        async def execute(_name: str, _args: dict) -> str:
            nonlocal executed
            executed += 1
            return "PORTABLE-FRESH-EVIDENCE"

        result = await router.complete(
            text_request("research", tools=(fetch_url_definition(),)),
            execute,
        )

        self.assertEqual(result.content, "fallback synthesis")
        self.assertEqual(executed, 1)
        request = fallback.requests[0]
        self.assertEqual(request.tools, ())
        self.assertFalse(
            any(
                isinstance(part, (ToolCallPart, ToolResultPart))
                for message in request.messages
                for part in message.parts
            )
        )
        self.assertIn("PORTABLE-FRESH-EVIDENCE", repr(request.messages))


class LearnedToolCapabilityScopeRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_no_tools_learning_does_not_disable_same_model_vision_tools(
        self,
    ) -> None:
        model = "shared-route-model"
        text = ScriptedProvider(
            "demo",
            [
                ProviderError(
                    "text route does not support tools",
                    unsupported_tools=True,
                    transient=False,
                ),
                ChatResponse(content="plain text"),
            ],
            model=model,
        )
        vision = ScriptedProvider(
            "demo",
            [ChatResponse(content="vision with tools available")],
            route="vision",
            max_images=1,
            target_id="demo:vision:shared-route-model:cred-1",
            model=model,
        )
        router = AIProviderRouter(
            [text, vision],
            text_provider_order=("demo",),
            vision_provider_order=("demo",),
        )
        tool = fetch_url_definition()

        first = await router.complete(
            text_request("text", tools=(tool,)),
            noop_tool,
        )
        second = await router.complete(
            text_request("vision", tools=(tool,)),
            noop_tool,
            requires_vision=True,
            image_count=1,
        )

        self.assertEqual(first.content, "plain text")
        self.assertEqual(second.content, "vision with tools available")
        self.assertEqual(vision.requests[0].tools, (tool,))


if __name__ == "__main__":
    unittest.main()
