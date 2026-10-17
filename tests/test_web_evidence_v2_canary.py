"""Canary scope for the experimental V2 web pipeline.

``WEB_EVIDENCE_V2_ENABLED`` stays the rollout switch; ``WEB_EVIDENCE_V2_GROUP_IDS``
promotes named chats inside one deployment. This suite pins both directions: a
listed chat runs V2 end to end (scope log, V2 system prompt, V2 synthesis frame,
V2 citation guard), and every other chat keeps the untouched settings object, so
its answer stays byte-for-byte the V1 answer.
"""

from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.router import CompletionResult
from app.bot.handlers import _ChatLocks, _handle_question
from app.bot.question_runner import QuestionProcessor, submit_controlled_question
from app.config import Settings
from app.core.context import ChatMemory
from app.core.orchestrator import Answer, Orchestrator
from app.core.rate_limiter import RateLimiter
from app.core.request import UserRequest
from app.core.stats import Stats
from app.search.citation_guard import UNVERIFIED_MARKER

QUERY = "Hãy xác minh: sứ mệnh Artemis III hạ cánh có người?"
EVIDENCE_URL = "https://www.nasa.gov/mission/artemis-iii"
INVENTED_URL = "https://www.nasa.gov/mission/artemis-iv"
EVIDENCE_HIT = {
    "title": "Artemis III mission page",
    "url": EVIDENCE_URL,
    "snippet": "Crewed flight in low Earth orbit.",
}
V2_GUIDANCE_MARKER = "KHI KIỂM CHỨNG THÔNG TIN TỪ WEB"
CANARY_CHAT_ID = 222
OTHER_CHAT_ID = 999


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class _RecordingRouter:
    """Calls ``web_search`` once, then answers with a fixed text."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.synthesis_modes: list[str] = []
        self.prompts: list[str] = []

    async def complete(self, request, tool_executor, **kwargs):
        self.synthesis_modes.append(request.synthesis_mode)
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


def _say_allowlist(
    chat_ids: str = f"{CANARY_CHAT_ID},{OTHER_CHAT_ID + 1}",
    *,
    enabled: bool = False,
    **overrides,
) -> Settings:
    return Settings(
        _env_file=None,
        search_backend="ddgs",
        web_evidence_v2_enabled=enabled,
        web_evidence_v2_group_ids=chat_ids,
        **overrides,
    )


class CanaryScopeResolutionTests(unittest.TestCase):
    def test_unlisted_and_missing_chats_reuse_the_identical_settings_object(self):
        base = _say_allowlist()
        for chat_id in (None, OTHER_CHAT_ID, 0):
            with self.subTest(chat_id=chat_id):
                resolved = base.for_chat(chat_id)
                self.assertIs(resolved, base)
                self.assertFalse(resolved.web_evidence_v2_enabled)

    def test_listed_chat_is_promoted_without_mutating_the_base_settings(self):
        base = _say_allowlist()
        promoted = base.for_chat(CANARY_CHAT_ID)
        self.assertIsNot(promoted, base)
        self.assertTrue(promoted.web_evidence_v2_enabled)
        self.assertFalse(base.web_evidence_v2_enabled)
        # Everything else about the chat's configuration is preserved.
        self.assertEqual(promoted.search_backend, base.search_backend)
        self.assertEqual(promoted.max_context_turns, base.max_context_turns)
        self.assertEqual(
            promoted.web_evidence_v2_group_ids, base.web_evidence_v2_group_ids
        )

    def test_global_flag_wins_over_the_allowlist(self):
        base = _say_allowlist(enabled=True)
        for chat_id in (None, OTHER_CHAT_ID, CANARY_CHAT_ID):
            with self.subTest(chat_id=chat_id):
                self.assertIs(base.for_chat(chat_id), base)

    def test_malformed_allowlist_entries_are_ignored(self):
        base = _say_allowlist(chat_ids=f" , abc,{CANARY_CHAT_ID} ,")
        self.assertTrue(base.for_chat(CANARY_CHAT_ID).web_evidence_v2_enabled)
        self.assertFalse(base.for_chat(OTHER_CHAT_ID).web_evidence_v2_enabled)

    def test_empty_allowlist_never_promotes(self):
        base = _say_allowlist(chat_ids="")
        self.assertIs(base.for_chat(CANARY_CHAT_ID), base)


class CanaryOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def _ask(self, settings: Settings, chat_id: int | None) -> dict:
        answer = f"Tổng quan: [NASA]({INVENTED_URL}) sẽ hạ cánh có người."
        router = _RecordingRouter(answer)
        orchestrator = Orchestrator(settings, router)
        search = AsyncMock(return_value=[dict(EVIDENCE_HIT)])
        capture = _LogCapture()
        logger = logging.getLogger("app.core.orchestrator")
        previous_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(capture)
        try:
            with patch("app.core.orchestrator.search_service.search", new=search):
                result = await orchestrator.ask(
                    request=UserRequest(text=QUERY, chat_id=chat_id)
                )
        finally:
            logger.removeHandler(capture)
            logger.setLevel(previous_level)
        return {
            "result": result,
            "raw_answer": answer,
            "prompt": router.prompts[0],
            "synthesis_mode": router.synthesis_modes[0],
            "search_calls": [call.kwargs for call in search.call_args_list],
            "logs": capture.messages,
        }

    async def test_listed_chat_runs_v2_and_logs_the_scope(self):
        run = await self._ask(_say_allowlist(), CANARY_CHAT_ID)
        self.assertIn(V2_GUIDANCE_MARKER, run["prompt"])
        self.assertEqual(run["synthesis_mode"], "v2")
        self.assertTrue(run["search_calls"])
        for kwargs in run["search_calls"]:
            self.assertTrue(kwargs.get("verification_context"))
        self.assertNotIn(INVENTED_URL, run["result"].text)
        self.assertIn(UNVERIFIED_MARKER, run["result"].text)
        self.assertIsNotNone(run["result"].citation_diagnostics)
        self.assertTrue(
            any(
                "v2_canary" in line and f"chat_id={CANARY_CHAT_ID}" in line
                for line in run["logs"]
            ),
            run["logs"],
        )

    async def test_unlisted_chat_keeps_the_v1_answer_byte_for_byte(self):
        run = await self._ask(_say_allowlist(), OTHER_CHAT_ID)
        self.assertEqual(run["result"].text, run["raw_answer"])
        self.assertIsNone(run["result"].citation_diagnostics)
        self.assertNotIn(V2_GUIDANCE_MARKER, run["prompt"])
        self.assertEqual(run["synthesis_mode"], "v1")
        self.assertTrue(run["search_calls"])
        for kwargs in run["search_calls"]:
            self.assertNotIn("verification_context", kwargs)
        self.assertFalse([line for line in run["logs"] if "v2_canary" in line])
        self.assertFalse([line for line in run["logs"] if "v2_citation_guard" in line])

    async def test_request_without_a_chat_id_keeps_v1(self):
        run = await self._ask(_say_allowlist(), None)
        self.assertEqual(run["result"].text, run["raw_answer"])
        self.assertEqual(run["synthesis_mode"], "v1")

    async def test_global_flag_still_serves_every_chat(self):
        run = await self._ask(_say_allowlist(enabled=True), OTHER_CHAT_ID)
        self.assertIn(V2_GUIDANCE_MARKER, run["prompt"])
        self.assertEqual(run["synthesis_mode"], "v2")
        self.assertIsNotNone(run["result"].citation_diagnostics)
        # The global switch is not the allowlist: no canary line is expected.
        self.assertFalse([line for line in run["logs"] if "v2_canary" in line])


class _StubOrchestrator:
    def __init__(self) -> None:
        self.requests: list[UserRequest] = []
        self.router = SimpleNamespace()

    async def ask(self, request=None, history=None, operations=None):
        self.requests.append(request)
        return SimpleNamespace(text="ok", images=None, searched=False, sources=[])


class _StubJobManager:
    def __init__(self) -> None:
        self.submissions = []

    async def submit(self, submission):
        self.submissions.append(submission)
        return "job-1"

    def get(self, job_id):
        return None


class CanaryWiringTests(unittest.IsolatedAsyncioTestCase):
    """The canary can only work if the chat id survives into the request."""

    async def test_prepared_request_carries_the_chat_id(self):
        settings = Settings(_env_file=None, bot_username="t")
        orchestrator = _StubOrchestrator()
        manager = _StubJobManager()
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            chat=SimpleNamespace(id=CANARY_CHAT_ID),
            message_thread_id=None,
            message_id=11,
            reply=AsyncMock(return_value=SimpleNamespace(message_id=12)),
        )
        operations = SimpleNamespace(run=AsyncMock(return_value=()))

        await submit_controlled_question(
            message,
            "hello",
            None,
            settings=settings,
            orchestrator=orchestrator,
            memory=SimpleNamespace(
                history_for=lambda key, limit: [],
                push_exchange=lambda *args, **kwargs: None,
            ),
            limiter=SimpleNamespace(allow=lambda user_id: (True, 0.0)),
            stats=SimpleNamespace(record_question=lambda: None),
            media_loader=SimpleNamespace(load=AsyncMock(return_value=())),
            manager=manager,
            presenter=SimpleNamespace(enqueue=lambda job_id, force=False: None),
        )

        submission = manager.submissions[0]
        request = await submission.prepare_request(operations)
        self.assertEqual(request.chat_id, CANARY_CHAT_ID)

    async def test_text_only_handler_passes_the_chat_id_to_the_orchestrator(self):
        """The ordinary text path must carry the chat id, not only the job/image paths."""
        settings = Settings(
            _env_file=None,
            bot_username="t",
            web_evidence_v2_group_ids=str(CANARY_CHAT_ID),
        )
        ask = AsyncMock(return_value=Answer("ok", "fake"))
        orchestrator = SimpleNamespace(
            router=SimpleNamespace(max_supported_images=lambda: 4),
            ask=ask,
        )
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            chat=SimpleNamespace(id=CANARY_CHAT_ID),
            message_thread_id=None,
            message_id=11,
            bot=SimpleNamespace(
                send_chat_action=AsyncMock(),
                send_message=AsyncMock(),
            ),
            reply=AsyncMock(return_value=SimpleNamespace(message_id=12)),
        )

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

        kwargs = ask.await_args.kwargs
        self.assertIn("request", kwargs)
        self.assertEqual(kwargs["request"].chat_id, CANARY_CHAT_ID)
        self.assertEqual(kwargs["request"].text, "hello")
        self.assertEqual(kwargs["history"], [])

    async def test_job_fallback_request_carries_the_submission_chat_id(self):
        orchestrator = _StubOrchestrator()
        processor = QuestionProcessor(
            bot=None,
            settings=Settings(_env_file=None, bot_username="t"),
            orchestrator=orchestrator,
            memory=None,
            stats=None,
        )
        record = SimpleNamespace(
            prepared_request=None,
            submission=SimpleNamespace(
                question="hello",
                quoted=None,
                history=[],
                chat_id=CANARY_CHAT_ID,
            ),
        )

        answer = await processor.execute(record, operations=None)

        self.assertEqual(answer.text, "ok")
        self.assertEqual(orchestrator.requests[0].chat_id, CANARY_CHAT_ID)


if __name__ == "__main__":
    unittest.main()
