import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.ai.base import AllProvidersFailed, OpenAICompatProvider, ProviderError
from app.ai.router import AIProviderRouter
from app.bot import job_status
from app.bot.question_runner import QuestionProcessor
from app.config import Settings
from app.core.job_manager import JobManager, JobSnapshot, JobSubmission, UserFacingJobError
from app.core.stats import Stats
from app.search import image_service, service
from app.search.router import RoutedSearchError, route_auto
from tests.provider_fakes import ScriptedProvider, noop_tool


class StatusRetryRegressionTests(unittest.TestCase):
    def test_retry_delay_caps_before_large_exponent_can_overflow(self):
        self.assertEqual(job_status._retry_delay(1), 0.5)
        self.assertEqual(job_status._retry_delay(2000), 30.0)


class _TerminalManager:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self, job_id):
        return self._snapshot if job_id == self._snapshot.job_id else None


class _AlwaysFailStatusBot:
    def __init__(self):
        self.attempts = 0

    async def edit_message_text(self, **kwargs):
        self.attempts += 1
        raise RuntimeError("telegram unavailable")


class StatusRetryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_status_retry_has_finite_horizon_and_cleans_state(self):
        snapshot = JobSnapshot(
            job_id="terminal-job",
            owner_id=10,
            chat_id=-100,
            topic_id=None,
            request_message_id=1,
            status_message_id=101,
            state="COMPLETED",
            generation=0,
            renewals=0,
            active_operations=0,
            created_at=0.0,
            active_duration=0.0,
            result_ready=True,
            error=None,
        )
        manager = _TerminalManager(snapshot)
        bot = _AlwaysFailStatusBot()
        presenter = job_status.JobStatusPresenter(bot, Settings(_env_file=None), manager)
        try:
            with (
                patch("app.bot.job_status._STATUS_RETRY_BASE_SEC", .001),
                patch("app.bot.job_status._STATUS_RETRY_MAX_SEC", .001),
                patch("app.bot.job_status._TERMINAL_STATUS_MAX_RETRIES", 2),
            ):
                presenter.enqueue(snapshot.job_id, force=True)
                while presenter._tasks:
                    await asyncio.gather(*tuple(presenter._tasks), return_exceptions=True)

            self.assertEqual(bot.attempts, 3)
            self.assertNotIn(snapshot.job_id, presenter._states)
            self.assertNotIn(snapshot.job_id, presenter._locks)
            self.assertNotIn(snapshot.job_id, presenter._scheduled)
        finally:
            await presenter.close()


class ConsentAdmissionRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_abandoned_consent_cancels_job_and_releases_admission(self):
        settings = Settings(
            _env_file=None,
            question_renewal_interval_sec=.02,
            question_progress_interval_sec=.005,
            question_max_pending_jobs=1,
            question_max_jobs_per_user=1,
            question_max_inflight_operations=1,
        )
        first_started = asyncio.Event()
        second_leaf_started = asyncio.Event()

        async def execute(record, operations):
            async def first_leaf():
                first_started.set()
                await asyncio.sleep(.03)
                return "first"

            async def second_leaf():
                second_leaf_started.set()
                return "second"

            await operations.run("first", first_leaf, timeout_sec=.2)
            await operations.run("second", second_leaf, timeout_sec=.2)
            return "answer"

        manager = JobManager(settings, execute)
        try:
            first = await manager.submit(
                JobSubmission("a", None, 10, -100, None, 1, 101, [])
            )
            await asyncio.wait_for(first_started.wait(), timeout=.2)
            record = manager.get(first)
            record.control.consent_timeout_sec = .01

            await asyncio.wait_for(manager.wait(first), timeout=.2)
            self.assertEqual(manager.snapshot(first).state, "CANCELLED")
            self.assertFalse(second_leaf_started.is_set())

            second = await manager.submit(
                JobSubmission("b", None, 10, -100, None, 2, 102, [])
            )
            self.assertNotEqual(first, second)
        finally:
            await manager.shutdown()


class ProviderFailurePrivacyRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_error_payload_does_not_escape_router_boundary(self):
        secret_payload = "SECRET_PROVIDER_RESPONSE_PAYLOAD"
        provider = ScriptedProvider(
            "leaky-provider",
            [ProviderError(f"leaky-provider HTTP 500: {secret_payload}")],
        )
        router = AIProviderRouter([provider])

        with self.assertLogs("app.ai.router", level="WARNING") as caught:
            with self.assertRaises(AllProvidersFailed) as raised:
                await router.complete(
                    [{"role": "user", "content": "question"}],
                    None,
                    noop_tool,
                )

        output = "\n".join(caught.output)
        self.assertIn("leaky-provider", output)
        self.assertNotIn(secret_payload, output)
        self.assertNotIn(secret_payload, str(raised.exception))

    async def test_http_error_body_is_not_stored_in_provider_exception(self):
        secret_payload = "SECRET_FAILED_GENERATION_PAYLOAD"

        def respond(_request):
            return httpx.Response(
                500,
                json={
                    "error": {
                        "message": "upstream generation failed",
                        "failed_generation": secret_payload,
                    }
                },
            )

        provider = OpenAICompatProvider(
            "privacy-test",
            "https://example.org/v1",
            "fake",
            "fake",
        )
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://example.org/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            with self.assertRaises(ProviderError) as raised:
                await provider.chat([{"role": "user", "content": "question"}])
            self.assertEqual(raised.exception.status_code, 500)
            self.assertNotIn(secret_payload, str(raised.exception))
        finally:
            await provider.aclose()


class SearchFailurePrivacyRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_backend_exception_payload_is_not_logged(self):
        secret_payload = "SECRET_SEARCH_BACKEND_PAYLOAD"
        settings = Settings(_env_file=None, searxng_url="http://searxng:8080")

        async def fail(_query, _settings, _limit):
            raise RuntimeError(secret_payload)

        with self.assertLogs("app.search.router", level="WARNING") as caught:
            with self.assertRaises(RoutedSearchError) as raised:
                await route_auto(
                    "private search query",
                    settings,
                    5,
                    kind="web",
                    result_url_key="url",
                    searx_call=fail,
                    ddgs_call=fail,
                )

        output = "\n".join(caught.output)
        self.assertNotIn(secret_payload, output)
        self.assertNotIn(secret_payload, str(raised.exception))

    def test_explicit_web_search_error_does_not_expose_backend_payload(self):
        secret_payload = "SECRET_EXPLICIT_WEB_BACKEND_PAYLOAD"
        with self.assertLogs("app.search.service", level="WARNING") as caught:
            error = service._search_error("searxng", RuntimeError(secret_payload))
        self.assertNotIn(secret_payload, "\n".join(caught.output))
        self.assertNotIn(secret_payload, str(error))

    def test_explicit_image_search_error_does_not_expose_backend_payload(self):
        secret_payload = "SECRET_EXPLICIT_IMAGE_BACKEND_PAYLOAD"
        with self.assertLogs("app.search.image_service", level="WARNING") as caught:
            error = image_service._image_search_error("ddgs", RuntimeError(secret_payload))
        self.assertNotIn(secret_payload, "\n".join(caught.output))
        self.assertNotIn(secret_payload, str(error))


class JobBoundaryLoggingRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_failure_logs_identity_without_submission_payload(self):
        secret_question = "SECRET_QUESTION_PAYLOAD"
        secret_history = "SECRET_HISTORY_PAYLOAD"
        secret_exception = "SECRET_EXCEPTION_PAYLOAD"

        async def execute(record, operations):
            raise RuntimeError(secret_exception)

        manager = JobManager(Settings(_env_file=None), execute)
        try:
            with self.assertLogs("app.core.job_manager", level="ERROR") as caught:
                job_id = await manager.submit(
                    JobSubmission(
                        secret_question,
                        "SECRET_QUOTED_PAYLOAD",
                        10,
                        -100,
                        None,
                        1,
                        101,
                        [{"role": "user", "content": secret_history}],
                    )
                )
                await asyncio.wait_for(manager.wait(job_id), timeout=.2)

            output = "\n".join(caught.output)
            self.assertIn(job_id, output)
            self.assertIn("RuntimeError", output)
            self.assertNotIn(secret_question, output)
            self.assertNotIn(secret_history, output)
            self.assertNotIn("SECRET_QUOTED_PAYLOAD", output)
            self.assertNotIn(secret_exception, output)
        finally:
            await manager.shutdown()


class _ExplodingOrchestrator:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def ask(self, **kwargs):
        raise RuntimeError(self.payload)


class QuestionProcessorPrivacyRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_provider_failure_does_not_retain_or_log_raw_exception(self):
        secret_exception = "SECRET_PROCESSOR_EXCEPTION_PAYLOAD"
        stats = Stats()
        processor = QuestionProcessor(
            bot=None,
            settings=Settings(_env_file=None),
            orchestrator=_ExplodingOrchestrator(secret_exception),
            memory=None,
            stats=stats,
        )
        record = SimpleNamespace(
            prepared_request=None,
            submission=SimpleNamespace(
                question="safe question",
                quoted=None,
                history=[],
            ),
        )

        with patch("app.bot.question_runner.logger.exception") as unsafe_log:
            with self.assertRaises(UserFacingJobError):
                await processor.execute(record, operations=None)

        unsafe_log.assert_not_called()
        self.assertEqual(stats.last_error, "RuntimeError")
        self.assertNotIn(secret_exception, stats.last_error or "")


if __name__ == "__main__":
    unittest.main()
