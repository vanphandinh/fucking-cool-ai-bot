"""Default-pipeline wiring: V2 is the only web policy and has no rollout state."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.router import CompletionResult
from app.bot.handlers import _ChatLocks, _handle_question
from app.config import Settings
from app.core.context import ChatMemory
from app.core.orchestrator import Answer, Orchestrator
from app.core.rate_limiter import RateLimiter
from app.core.request import UserRequest
from app.core.stats import Stats
from app.search.citation_guard import UNVERIFIED_MARKER
from app.search.url_service import UrlReadResult

QUERY = "Hãy xác minh: sứ mệnh Artemis III hạ cánh có người?"
EVIDENCE_URL = "https://www.nasa.gov/mission/artemis-iii"
INVENTED_URL = "https://www.nasa.gov/mission/artemis-iv"
EVIDENCE_HIT = {
    "title": "Artemis III mission page",
    "url": EVIDENCE_URL,
    "snippet": "Crewed flight in low Earth orbit.",
}
V2_GUIDANCE_MARKER = "KHI KIỂM CHỨNG THÔNG TIN TỪ WEB"


class ConfigSurfaceTests(unittest.TestCase):
    def test_retired_rollout_fields_are_gone(self):
        fields = Settings.model_fields
        for name in (
            "web_evidence_v2_enabled",
            "web_evidence_v2_group_ids",
            "web_auto_fetch_enabled",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, fields)

    def test_auto_fetch_bounds_keep_their_defaults(self):
        settings = Settings(_env_file=None)
        self.assertEqual(settings.web_auto_fetch_max_sources, 2)
        self.assertEqual(settings.web_auto_fetch_budget_sec, 20.0)
        self.assertEqual(settings.web_evidence_max_extracted_chars, 120000)


class _RecordingRouter:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    async def complete(self, request, tool_executor, **kwargs):
        self.prompts.append(
            "\n".join(
                part.text
                for message in request.messages
                for part in message.parts
                if getattr(part, "text", "")
            )
        )
        await tool_executor("web_search", {"query": QUERY})
        return CompletionResult(self.answer, "fake", ())


class DefaultOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_guard_and_verification_are_always_on(self):
        answer = f"Tổng quan: [NASA]({INVENTED_URL}) sẽ hạ cánh có người."
        router = _RecordingRouter(answer)
        orchestrator = Orchestrator(Settings(_env_file=None), router)
        search = AsyncMock(return_value=[dict(EVIDENCE_HIT)])
        read = AsyncMock(
            return_value=UrlReadResult("body text", EVIDENCE_URL, "generic_reader", True)
        )
        with (
            patch("app.core.orchestrator.search_service.search", new=search),
            patch("app.core.orchestrator.url_service.read_url", new=read),
        ):
            result = await orchestrator.ask(QUERY)

        self.assertIn(V2_GUIDANCE_MARKER, router.prompts[0])
        self.assertTrue(search.call_args.kwargs.get("verification_context"))
        self.assertNotIn(INVENTED_URL, result.text)
        self.assertIn(UNVERIFIED_MARKER, result.text)
        self.assertIsNotNone(result.citation_diagnostics)

    async def test_system_prompt_takes_no_settings_override(self):
        orchestrator = Orchestrator(Settings(_env_file=None), None)
        self.assertIn(V2_GUIDANCE_MARKER, orchestrator.system_prompt())
        with self.assertRaises(TypeError):
            orchestrator.system_prompt(Settings(_env_file=None))


class HandlerRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_handler_passes_a_request_without_chat_id(self):
        captured: list[UserRequest] = []

        async def ask(**kwargs):
            captured.append(kwargs["request"])
            return Answer(text="ok", provider="fake")

        settings = Settings(_env_file=None, question_timeout_sec=2.0)
        orchestrator = SimpleNamespace(
            router=SimpleNamespace(max_supported_images=lambda: 4),
            ask=ask,
        )
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            chat=SimpleNamespace(id=-100200),
            message_thread_id=None,
            message_id=11,
            bot=SimpleNamespace(
                send_chat_action=AsyncMock(),
                send_message=AsyncMock(),
            ),
            reply=AsyncMock(return_value=SimpleNamespace(message_id=12)),
        )

        with patch("app.bot.handlers._send_answer_parts", new=AsyncMock(return_value=True)):
            await _handle_question(
                message,
                "hello",
                None,
                settings,
                orchestrator,
                ChatMemory(),
                RateLimiter(),
                Stats(),
                _ChatLocks(),
                media_loader=SimpleNamespace(load=AsyncMock(return_value=())),
            )

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].text, "hello")
        self.assertFalse(hasattr(captured[0], "chat_id"))


if __name__ == "__main__":
    unittest.main()
