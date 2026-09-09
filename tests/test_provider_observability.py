"""Generic provider observability and application UX regressions."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from app.ai.base import ChatResponse
from app.ai.router import AIProviderRouter, CompletionResult
from app.bot.handlers import _provider_status_lines, _vision_limit_message
from app.config import Settings
from app.core.orchestrator import Orchestrator
from app.core.stats import Stats
from tests.provider_fakes import ScriptedProvider


class ProviderObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_orchestrator_propagates_request_local_fallbacks(self) -> None:
        router = SimpleNamespace(
            complete=AsyncMock(
                return_value=CompletionResult(
                    content="answer",
                    provider="second",
                    fallbacks=("second",),
                )
            )
        )
        answer = await Orchestrator(Settings(_env_file=None), router).ask(
            question="hello"
        )
        self.assertEqual(answer.provider, "second")
        self.assertEqual(answer.fallbacks, ("second",))

    async def test_status_labels_are_provider_generic(self) -> None:
        provider = ScriptedProvider("fake", [ChatResponse(content="ok")])
        router = AIProviderRouter(
            [provider],
            text_provider_order=("fake",),
            vision_provider_order=(),
        )
        lines = "\n".join(_provider_status_lines(router, Stats()))
        self.assertIn("Text providers: fake", lines)
        self.assertIn("Vision providers: disabled", lines)
        self.assertIn("Fallbacks: 0", lines)
        self.assertNotIn("B.AI text", lines)


class FallbackStatsTests(unittest.TestCase):
    def test_stats_count_explicit_provider_transitions(self) -> None:
        stats = Stats()
        stats.record_fallbacks(("second", "third"))
        self.assertEqual(stats.fallback_count, 2)

    def test_vision_limit_uses_application_and_provider_caps(self) -> None:
        provider = ScriptedProvider(
            "fake",
            [ChatResponse(content="ok")],
            route="vision",
            max_images=4,
        )
        router = AIProviderRouter(
            [provider],
            text_provider_order=(),
            vision_provider_order=("fake",),
        )
        settings = Settings(_env_file=None, max_images_per_request=3)
        self.assertIn("tối đa 3 ảnh", _vision_limit_message(settings, router))


if __name__ == "__main__":
    unittest.main()
