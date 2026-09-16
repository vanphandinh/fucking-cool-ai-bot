"""Generic provider observability and application UX regressions."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.ai.base import AllProvidersFailed, ChatResponse, ProviderError
from app.ai.recovery import HealthEffect, HealthScope
from app.ai.router import AIProviderRouter, CompletionResult
from app.ai.target import provider_target_identity
from app.bot.handlers import _provider_status_lines, _vision_limit_message
from app.bot.question_runner import QuestionProcessor
from app.config import Settings
from app.core.job_manager import UserFacingJobError
from app.core.orchestrator import Orchestrator
from app.core.request import UserRequest
from app.core.stats import Stats
from tests.provider_fakes import ScriptedProvider, noop_tool


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

    async def test_orchestrator_propagates_target_recovery_counters(self) -> None:
        router = SimpleNamespace(
            complete=AsyncMock(
                return_value=CompletionResult(
                    content="answer",
                    provider="chainnode",
                    target_rotations=3,
                    model_rotations=2,
                    credential_failovers=1,
                )
            )
        )

        answer = await Orchestrator(Settings(_env_file=None), router).ask(
            question="hello"
        )

        self.assertEqual(answer.target_rotations, 3)
        self.assertEqual(answer.model_rotations, 2)
        self.assertEqual(answer.credential_failovers, 1)

    async def test_question_processor_records_target_recovery_counters(self) -> None:
        stats = Stats()
        memory = SimpleNamespace(push_exchange=MagicMock())
        processor = QuestionProcessor(
            bot=SimpleNamespace(),
            settings=Settings(_env_file=None),
            orchestrator=SimpleNamespace(),
            memory=memory,
            stats=stats,
        )
        processor._send_answer_parts = AsyncMock()
        submission = SimpleNamespace(
            question="hello",
            chat_id=1,
            topic_id=None,
            request_message_id=10,
        )
        record = SimpleNamespace(
            prepared_request=None,
            submission=submission,
            delivery_committed=False,
        )
        answer = SimpleNamespace(
            text="answer",
            provider="chainnode",
            fallbacks=(),
            searched=False,
            sources=[],
            images=[],
            target_rotations=3,
            model_rotations=2,
            credential_failovers=1,
        )

        await processor.deliver(record, answer)

        self.assertEqual(stats.target_rotation_count, 3)
        self.assertEqual(stats.model_rotation_count, 2)
        self.assertEqual(stats.credential_failover_count, 1)
        self.assertTrue(record.delivery_committed)

    async def test_terminal_provider_failure_carries_target_recovery_counters(self) -> None:
        first = ScriptedProvider(
            "chainnode",
            [ProviderError("quota-a", status_code=429, transient=True)],
        )
        first.model = "m1"
        first.credential_id = "c1"
        first.target_id = "chainnode:text:m1:c1"
        second = ScriptedProvider(
            "chainnode",
            [ProviderError("quota-b", status_code=429, transient=True)],
        )
        second.model = "m2"
        second.credential_id = "c1"
        second.target_id = "chainnode:text:m2:c1"
        router = AIProviderRouter(
            [first, second],
            text_provider_order=("chainnode",),
            vision_provider_order=(),
        )

        with self.assertRaises(AllProvidersFailed) as caught:
            await router.complete([{"role": "user", "content": "hello"}], None, noop_tool)

        exc = caught.exception
        self.assertEqual(exc.target_rotations, 1)
        self.assertEqual(exc.model_rotations, 1)
        self.assertEqual(exc.credential_failovers, 0)

    async def test_question_processor_records_target_recovery_on_all_providers_failed(self) -> None:
        stats = Stats()
        orchestrator = SimpleNamespace(
            ask=AsyncMock(
                side_effect=AllProvidersFailed(
                    "failed",
                    fallbacks=("xkiro",),
                    target_rotations=3,
                    model_rotations=2,
                    credential_failovers=1,
                )
            )
        )
        processor = QuestionProcessor(
            bot=SimpleNamespace(),
            settings=Settings(_env_file=None),
            orchestrator=orchestrator,
            memory=SimpleNamespace(),
            stats=stats,
        )
        record = SimpleNamespace(
            prepared_request=UserRequest(text="hello"),
            submission=SimpleNamespace(history=[]),
        )

        with self.assertRaises(UserFacingJobError):
            await processor.execute(record, operations=None)

        self.assertEqual(stats.fallback_count, 1)
        self.assertEqual(stats.target_rotation_count, 3)
        self.assertEqual(stats.model_rotation_count, 2)
        self.assertEqual(stats.credential_failover_count, 1)

    async def test_status_labels_are_provider_generic_and_show_target_recovery(self) -> None:
        provider = ScriptedProvider("fake", [ChatResponse(content="ok")])
        router = AIProviderRouter(
            [provider],
            text_provider_order=("fake",),
            vision_provider_order=(),
        )
        stats = Stats()
        stats.record_target_recovery(
            target_rotations=3,
            model_rotations=2,
            credential_failovers=1,
        )
        lines = "\n".join(_provider_status_lines(router, stats))
        self.assertIn("Text providers: fake", lines)
        self.assertIn("Vision providers: disabled", lines)
        self.assertIn("Fallbacks: 0", lines)
        self.assertIn("Target rotations: 3 (models=2, credentials=1)", lines)
        self.assertNotIn("B.AI text", lines)

    async def test_status_reports_scoped_target_unavailability_without_secrets(self) -> None:
        denied = ScriptedProvider("xkiro", [ChatResponse(content="unused")])
        denied.model = "model-a"
        denied.credential_id = "cred-1"
        denied.target_id = "xkiro:text:m1:c1"
        denied.api_key = "RAW-SECRET-MUST-NOT-LEAK"
        sibling = ScriptedProvider("xkiro", [ChatResponse(content="unused")])
        sibling.model = "model-a"
        sibling.credential_id = "cred-2"
        sibling.target_id = "xkiro:text:m1:c2"
        router = AIProviderRouter(
            [denied, sibling],
            text_provider_order=("xkiro",),
            vision_provider_order=(),
        )
        router.scoped_health.record_effect(
            HealthScope.ENTITLEMENT,
            provider_target_identity(denied),
            "entitlement denied for RAW-SECRET-MUST-NOT-LEAK",
            effect=HealthEffect.DISABLE,
        )

        lines = "\n".join(_provider_status_lines(router, Stats()))

        self.assertIn("xkiro:text:m1:c1=scoped-disabled", lines)
        self.assertNotIn("xkiro:text:m1:c2=", lines)
        self.assertNotIn("RAW-SECRET-MUST-NOT-LEAK", lines)


class FallbackStatsTests(unittest.TestCase):
    def test_stats_count_explicit_provider_transitions(self) -> None:
        stats = Stats()
        stats.record_fallbacks(("second", "third"))
        self.assertEqual(stats.fallback_count, 2)

    def test_stats_count_target_recovery_separately_from_fallbacks(self) -> None:
        stats = Stats()
        stats.record_target_recovery(
            target_rotations=3,
            model_rotations=2,
            credential_failovers=1,
        )
        self.assertEqual(stats.fallback_count, 0)
        self.assertEqual(stats.target_rotation_count, 3)
        self.assertEqual(stats.model_rotation_count, 2)
        self.assertEqual(stats.credential_failover_count, 1)

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
