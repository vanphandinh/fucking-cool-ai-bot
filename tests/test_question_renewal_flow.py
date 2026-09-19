import asyncio
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.ai.base import ProviderError, TransportFailureKind
from app.ai.contracts import ChatResponse, ToolCallPart as ToolCall
from app.ai.router import AIProviderRouter, CompletionResult
from app.config import Settings
from app.core.job_manager import JobManager, JobSubmission
from app.core.orchestrator import Orchestrator
from app.search.url_service import UrlReadResult
from tests.provider_fakes import ScriptedProvider, fetch_url_definition, text_request


class QuestionRenewalFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        manager = getattr(self, "manager", None)
        if manager is not None:
            await manager.shutdown()

    async def _renew_at_boundary(self, job_id, submission, boundary):
        await asyncio.wait_for(boundary.wait(), timeout=.5)
        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "AWAITING_CONSENT")
        self.assertEqual(
            await self.manager.renew(
                job_id,
                snapshot.generation,
                submission.owner_id,
                submission.chat_id,
                submission.status_message_id,
            ),
            "renewed",
        )

    async def test_two_renewals_keep_same_job_and_do_not_replay_completed_steps(self):
        settings = Settings(
            _env_file=None,
            question_renewal_interval_sec=1,
            question_progress_interval_sec=.1,
            question_max_inflight_operations=1,
        )
        entered = [asyncio.Event() for _ in range(3)]
        release = [asyncio.Event() for _ in range(3)]
        calls = [0, 0, 0]

        async def execute(record, operations):
            evidence = []
            for index in range(3):
                async def step(i=index):
                    calls[i] += 1
                    entered[i].set()
                    await release[i].wait()
                    return f"evidence-{i}"

                evidence.append(
                    await operations.run(f"step-{index}", step, timeout_sec=1)
                )
            return "|".join(evidence)

        self.manager = JobManager(settings, execute)
        job_id = await self.manager.submit(
            JobSubmission("q", None, 10, -100, 7, 1, 101, [])
        )
        record = self.manager.get(job_id)

        await entered[0].wait()
        record.control.remaining = 0
        self.assertTrue(await record.control.expire_if_due())
        release[0].set()
        await asyncio.sleep(.01)
        self.assertFalse(entered[1].is_set())
        generation1 = record.control.generation
        self.assertEqual(
            await self.manager.renew(job_id, generation1, 10, -100, 101),
            "renewed",
        )

        await entered[1].wait()
        record.control.remaining = 0
        self.assertTrue(await record.control.expire_if_due())
        release[1].set()
        await asyncio.sleep(.01)
        self.assertFalse(entered[2].is_set())
        generation2 = record.control.generation
        self.assertEqual(
            await self.manager.renew(job_id, generation2, 10, -100, 101),
            "renewed",
        )

        await entered[2].wait()
        release[2].set()
        await self.manager.wait(job_id)
        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.renewals, 2)
        self.assertEqual(calls, [1, 1, 1])
        self.assertEqual(self.manager.owned_task_count, 1)  # monitor until shutdown
        await self.manager.shutdown()
        self.assertEqual(self.manager.owned_task_count, 0)
        self.manager = None

    async def test_orchestrator_url_cache_survives_expiry_without_refetch(self):
        settings = Settings(
            _env_file=None,
            question_renewal_interval_sec=1,
            question_progress_interval_sec=.1,
        )
        control_holder = {}

        class FakeRouter:
            async def complete(
                self,
                request,
                tool_executor,
                *,
                requires_vision=False,
                image_count=0,
                operations=None,
            ):
                first = await tool_executor("fetch_url", {"url": "https://example.com"})
                second = await tool_executor("fetch_url", {"url": "https://example.com"})
                control_holder["control"] = operations.control
                self.first = first
                self.second = second
                return CompletionResult("answer", "fake")

        fake_router = FakeRouter()
        orchestrator = Orchestrator(settings, fake_router)
        from app.core.job_control import JobControl
        from app.core.job_operations import OperationRunner

        control = JobControl(renewal_sec=1)
        operations = OperationRunner(control, asyncio.Semaphore(1), lambda event: None)
        read_result = SimpleNamespace(
            ok=True,
            source_url="https://example.com",
            text="evidence",
        )

        async def controlled_read(url, current_settings, mode="auto", *, operations=None):
            self.assertEqual(url, "https://example.com")
            self.assertIs(current_settings, settings)
            self.assertEqual(mode, "auto")
            self.assertIsNotNone(operations)

            async def leaf():
                operations.control.remaining = 0
                self.assertTrue(await operations.control.expire_if_due())
                return read_result

            return await operations.run("mock URL", leaf, timeout_sec=.1)

        read_mock = AsyncMock(side_effect=controlled_read)
        with patch("app.core.orchestrator.url_service.read_url", read_mock):
            answer = await orchestrator.ask("q", operations=operations)

        self.assertEqual(answer.text, "answer")
        self.assertEqual(fake_router.first, fake_router.second)
        self.assertEqual(read_mock.await_count, 1)
        self.assertEqual(control_holder["control"].state, "AWAITING_CONSENT")

    async def test_provider_tools_resume_across_two_generations_without_replay(self):
        settings = Settings(
            _env_file=None,
            question_renewal_interval_sec=1,
            question_progress_interval_sec=.1,
            question_max_inflight_operations=2,
            ai_attempt_total_timeout_sec=.5,
            url_read_total_timeout_sec=.4,
            tool_call_total_timeout_sec=.5,
            max_tool_rounds=5,
        )
        url1 = "https://example.com/one"
        url2 = "https://example.com/two"
        chainnode = ScriptedProvider(
            "chainnode",
            [
                ProviderError(
                    "chainnode read timeout",
                    transport_kind=TransportFailureKind.READ_TIMEOUT,
                )
            ],
        )
        xkiro = ScriptedProvider(
            "xkiro",
            [
                ChatResponse(
                    tool_calls=[
                        ToolCall(
                            id="url-one",
                            name="fetch_url",
                            arguments={"url": url1},
                        )
                    ]
                ),
                ChatResponse(
                    tool_calls=[
                        ToolCall(
                            id="url-two",
                            name="fetch_url",
                            arguments={"url": url2},
                        )
                    ]
                ),
                ChatResponse(
                    tool_calls=[
                        ToolCall(
                            id="url-one-cached",
                            name="fetch_url",
                            arguments={"url": url1},
                        )
                    ]
                ),
                ChatResponse(content="final answer"),
            ],
        )
        provider_router = AIProviderRouter(
            [chainnode, xkiro],
            text_provider_order=("chainnode", "xkiro"),
            vision_provider_order=(),
            max_tool_rounds=5,
        )
        orchestrator = Orchestrator(settings, provider_router)
        fetch_counts = Counter()
        first_boundary = asyncio.Event()
        second_boundary = asyncio.Event()
        final_answers = []

        async def controlled_read(url, current_settings, mode="auto", *, operations=None):
            self.assertIs(current_settings, settings)
            self.assertEqual(mode, "auto")
            self.assertIsNotNone(operations)
            fetch_counts[url] += 1

            async def leaf():
                operations.control.remaining = 0
                self.assertTrue(await operations.control.expire_if_due())
                if url == url2:
                    await asyncio.sleep(0)
                    return UrlReadResult(
                        "Không tải được trang: HTTP 403",
                        url,
                        "generic_reader",
                        False,
                    )
                return UrlReadResult("evidence-one", url, "generic_reader", True)

            result = await operations.run(f"read {url}", leaf, timeout_sec=.2)
            if url == url1:
                first_boundary.set()
            else:
                second_boundary.set()
            return result

        async def execute(record, operations):
            return await orchestrator.ask(
                record.submission.question,
                history=record.submission.history,
                operations=operations,
            )

        async def deliver(record, answer):
            final_answers.append((record.submission.topic_id, answer.text))

        self.manager = JobManager(settings, execute, deliver)
        submission = JobSubmission(
            "research",
            None,
            10,
            -100,
            7,
            1,
            101,
            [{"role": "user", "content": "older context"}],
        )

        with patch("app.core.orchestrator.url_service.read_url", new=controlled_read):
            job_id = await self.manager.submit(submission)

            await self._renew_at_boundary(job_id, submission, first_boundary)
            await self._renew_at_boundary(job_id, submission, second_boundary)
            await asyncio.wait_for(self.manager.wait(job_id), timeout=.5)

        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.renewals, 2)
        self.assertEqual(fetch_counts[url1], 1)
        self.assertEqual(fetch_counts[url2], 1)
        self.assertEqual(len(chainnode.requests), 1)
        self.assertEqual(len(xkiro.requests), 4)
        self.assertEqual(final_answers, [(7, "final answer")])

        await self.manager.shutdown()
        self.assertEqual(self.manager.owned_task_count, 0)
        self.manager = None


    async def test_continue_does_not_reset_five_round_tool_budget(self):
        settings = Settings(
            _env_file=None,
            question_renewal_interval_sec=1,
            question_progress_interval_sec=.1,
            question_max_inflight_operations=1,
            ai_attempt_total_timeout_sec=.5,
            url_read_total_timeout_sec=.4,
            tool_call_total_timeout_sec=.5,
            max_tool_rounds=5,
        )

        class RenewalBudgetProvider(ScriptedProvider):
            def __init__(self):
                super().__init__("xkiro", [])
                self.tool_rounds_requested = 0
                self.synthesis_requests = 0

            async def chat(self, request):
                self.requests.append(request)
                if not request.tools:
                    self.synthesis_requests += 1
                    return ChatResponse(content="final after five rounds")
                self.tool_rounds_requested += 1
                index = self.tool_rounds_requested
                return ChatResponse(
                    tool_calls=[
                        ToolCall(
                            id=f"url-{index}",
                            name="fetch_url",
                            arguments={"url": f"https://example.com/{index}"},
                        )
                    ]
                )

        provider = RenewalBudgetProvider()
        provider_router = AIProviderRouter(
            [provider],
            text_provider_order=("xkiro",),
            vision_provider_order=(),
            max_tool_rounds=5,
        )
        orchestrator = Orchestrator(settings, provider_router)
        first_boundary = asyncio.Event()
        second_boundary = asyncio.Event()
        fetch_count = 0
        final_answers = []

        async def controlled_read(url, current_settings, mode="auto", *, operations=None):
            nonlocal fetch_count
            self.assertIs(current_settings, settings)
            self.assertEqual(mode, "auto")
            self.assertIsNotNone(operations)
            fetch_count += 1
            call_number = fetch_count

            async def leaf():
                if call_number in (2, 4):
                    operations.control.remaining = 0
                    self.assertTrue(await operations.control.expire_if_due())
                return UrlReadResult(
                    f"evidence-{call_number}",
                    url,
                    "generic_reader",
                    True,
                )

            result = await operations.run(f"read {url}", leaf, timeout_sec=.2)
            if call_number == 2:
                first_boundary.set()
            elif call_number == 4:
                second_boundary.set()
            return result

        async def execute(record, operations):
            return await orchestrator.ask(
                record.submission.question,
                history=record.submission.history,
                operations=operations,
            )

        async def deliver(record, answer):
            final_answers.append(answer.text)

        self.manager = JobManager(settings, execute, deliver)
        submission = JobSubmission("research", None, 10, -100, 7, 1, 101, [])

        with patch("app.core.orchestrator.url_service.read_url", new=controlled_read):
            job_id = await self.manager.submit(submission)

            await self._renew_at_boundary(job_id, submission, first_boundary)
            await self._renew_at_boundary(job_id, submission, second_boundary)

            await asyncio.wait_for(self.manager.wait(job_id), timeout=.5)

        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.renewals, 2)
        self.assertEqual(fetch_count, 5)
        self.assertEqual(provider.tool_rounds_requested, 5)
        self.assertEqual(provider.synthesis_requests, 1)
        self.assertEqual(final_answers, ["final after five rounds"])

        await self.manager.shutdown()
        self.assertEqual(self.manager.owned_task_count, 0)
        self.manager = None


    async def test_continue_does_not_reset_twelve_call_budget(self):
        settings = Settings(
            _env_file=None,
            question_renewal_interval_sec=1,
            question_progress_interval_sec=.1,
            question_max_inflight_operations=1,
            ai_attempt_total_timeout_sec=.5,
            url_read_total_timeout_sec=.4,
            tool_call_total_timeout_sec=.5,
            max_tool_rounds=5,
        )
        provider = ScriptedProvider(
            "xkiro",
            [
                ChatResponse(
                    tool_calls=[
                        ToolCall(
                            id=f"pre-{index}",
                            name="fetch_url",
                            arguments={"url": f"https://example.com/pre/{index}"},
                        )
                        for index in range(10)
                    ]
                ),
                ChatResponse(
                    tool_calls=[
                        ToolCall(
                            id=f"overflow-{index}",
                            name="fetch_url",
                            arguments={"url": f"https://example.com/overflow/{index}"},
                        )
                        for index in range(3)
                    ]
                ),
                ChatResponse(content="final after call cap"),
            ],
        )
        provider_router = AIProviderRouter(
            [provider],
            text_provider_order=("xkiro",),
            vision_provider_order=(),
            max_tool_rounds=5,
        )
        boundary = asyncio.Event()
        executed_urls = []
        final_answers = []

        async def execute(record, operations):
            async def tool_executor(_name, args):
                url = str(args["url"])

                async def leaf():
                    executed_urls.append(url)
                    if len(executed_urls) == 10:
                        operations.control.remaining = 0
                        self.assertTrue(await operations.control.expire_if_due())
                    return f"evidence:{url}"

                result = await operations.run(f"read {url}", leaf, timeout_sec=.2)
                if len(executed_urls) == 10:
                    boundary.set()
                return result

            return await provider_router.complete(
                text_request(
                    record.submission.question,
                    tools=(fetch_url_definition(),),
                ),
                tool_executor,
                operations=operations,
            )

        async def deliver(_record, answer):
            final_answers.append(answer.content)

        self.manager = JobManager(settings, execute, deliver)
        submission = JobSubmission("research", None, 10, -100, 7, 1, 101, [])
        job_id = await self.manager.submit(submission)

        await self._renew_at_boundary(job_id, submission, boundary)
        await asyncio.wait_for(self.manager.wait(job_id), timeout=.5)

        snapshot = self.manager.snapshot(job_id)
        self.assertEqual(snapshot.state, "COMPLETED")
        self.assertEqual(snapshot.renewals, 1)
        self.assertEqual(len(executed_urls), 10)
        self.assertEqual(len(provider.requests), 3)
        self.assertTrue(provider.requests[0].tools)
        self.assertTrue(provider.requests[1].tools)
        self.assertEqual(provider.requests[2].tools, ())
        self.assertEqual(final_answers, ["final after call cap"])

        await self.manager.shutdown()
        self.assertEqual(self.manager.owned_task_count, 0)
        self.manager = None


if __name__ == "__main__":
    unittest.main()
