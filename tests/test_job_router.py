import asyncio
import unittest

from app.ai.contracts import ChatRequest, ChatResponse, ToolCallPart as ToolCall
from app.ai.router import AIProviderRouter, _execute_tool_batch
from app.core.job_control import JobControl
from app.core.job_operations import OperationRunner, _budgets
from tests.provider_fakes import ScriptedProvider, fetch_url_definition, text_request


class RenewableRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_response_after_expiry_is_delivered_without_renew(self):
        now = [0.0]
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowProvider(ScriptedProvider):
            async def chat(self, request: ChatRequest):
                entered.set()
                await release.wait()
                return ChatResponse(content="done")

        provider = SlowProvider("one", [])
        router = AIProviderRouter([provider])
        control = JobControl(renewal_sec=1, clock=lambda: now[0])
        operations = OperationRunner(
            control,
            asyncio.Semaphore(1),
            lambda event: None,
        )
        task = asyncio.create_task(
            router.complete(
                text_request("q"),
                lambda name, args: asyncio.sleep(0, result="unused"),
                operations=operations,
            )
        )
        await entered.wait()
        now[0] = 2
        await control.expire_if_due()
        release.set()
        result = await asyncio.wait_for(task, .2)
        self.assertEqual(result.content, "done")
        self.assertEqual(control.state, "AWAITING_CONSENT")

    async def test_tool_call_after_expiry_waits_before_dispatch_and_resumes_same_state(self):
        now = [0.0]
        entered = asyncio.Event()
        release = asyncio.Event()
        tool_calls = []

        class SlowProvider(ScriptedProvider):
            def __init__(self):
                super().__init__("one", [])
                self.turn = 0

            async def chat(self, request: ChatRequest):
                self.turn += 1
                if self.turn == 1:
                    entered.set()
                    await release.wait()
                    return ChatResponse(
                        tool_calls=[
                            ToolCall(
                                id="t1",
                                name="fetch_url",
                                arguments={"url": "https://a"},
                            )
                        ]
                    )
                return ChatResponse(content="answer")

        async def tool(name, args):
            tool_calls.append((name, args["url"]))
            return "evidence"

        provider = SlowProvider()
        router = AIProviderRouter([provider])
        control = JobControl(renewal_sec=1, clock=lambda: now[0])
        operations = OperationRunner(
            control,
            asyncio.Semaphore(1),
            lambda event: None,
        )
        task = asyncio.create_task(
            router.complete(
                text_request("q", tools=(fetch_url_definition(),)),
                tool,
                operations=operations,
            )
        )
        await entered.wait()
        now[0] = 2
        await control.expire_if_due()
        release.set()
        await asyncio.sleep(.03)
        self.assertEqual(tool_calls, [])
        self.assertFalse(task.done())

        self.assertTrue(await control.renew(control.generation))
        result = await asyncio.wait_for(task, .2)
        self.assertEqual(result.content, "answer")
        self.assertEqual(tool_calls, [("fetch_url", "https://a")])
        self.assertEqual(provider.turn, 2)

    async def test_logical_tool_call_shares_one_aggregate_leaf_budget(self):
        control = JobControl(renewal_sec=10)
        operations = OperationRunner(
            control,
            asyncio.Semaphore(1),
            lambda event: None,
            tool_timeout=.02,
        )
        observed = {}

        async def tool(name, args):
            inherited = _budgets.get()
            observed["budget_count"] = len(inherited)
            budget = inherited[0]

            async def first_leaf():
                await asyncio.sleep(.012)
                return "first"

            await operations.run("leaf-1", first_leaf, timeout_sec=.1)
            observed["after_first"] = budget.remaining

            async def blocked_leaf():
                await asyncio.Event().wait()

            with self.assertRaises(TimeoutError):
                await operations.run("leaf-2", blocked_leaf, timeout_sec=.1)
            observed["after_second"] = budget.remaining
            return "budget enforced"

        output = await _execute_tool_batch(
            [ToolCall(id="t1", name="composite", arguments={})],
            tool,
            operations=operations,
        )

        self.assertEqual(output, ["budget enforced"])
        self.assertEqual(observed["budget_count"], 1)
        self.assertLess(observed["after_first"], operations.tool_timeout)
        self.assertLessEqual(observed["after_second"], 0)

    async def test_tool_budget_pauses_while_waiting_for_consent(self):
        now = [0.0]
        control = JobControl(renewal_sec=1, clock=lambda: now[0])
        operations = OperationRunner(
            control,
            asyncio.Semaphore(1),
            lambda event: None,
            tool_timeout=.02,
        )
        consent_reached = asyncio.Event()

        async def tool(name, args):
            async def first_leaf():
                await asyncio.sleep(.005)
                now[0] = 2.0
                return "first"

            await operations.run("leaf-1", first_leaf, timeout_sec=.1)
            consent_reached.set()

            async def second_leaf():
                await asyncio.sleep(.001)
                return "second"

            result = await operations.run("leaf-2", second_leaf, timeout_sec=.1)
            self.assertEqual(len(_budgets.get()), 1)
            return result

        task = asyncio.create_task(
            _execute_tool_batch(
                [ToolCall(id="t1", name="composite", arguments={})],
                tool,
                operations=operations,
            )
        )
        await asyncio.wait_for(consent_reached.wait(), timeout=.1)
        self.assertEqual(control.state, "AWAITING_CONSENT")
        await asyncio.sleep(.04)
        self.assertFalse(task.done())

        self.assertTrue(await control.renew(control.generation))
        output = await asyncio.wait_for(task, timeout=.1)
        self.assertEqual(output, ["second"])

    async def test_local_ai_timeout_is_classified_retryable_read_timeout(self):
        from app.ai.base import AllProvidersFailed

        class HungProvider(ScriptedProvider):
            async def chat(self, request: ChatRequest):
                await asyncio.Event().wait()

        provider = HungProvider("hung", [])
        router = AIProviderRouter([provider])
        control = JobControl(renewal_sec=10)
        operations = OperationRunner(
            control,
            asyncio.Semaphore(1),
            lambda event: None,
            ai_timeout=.01,
        )
        with self.assertRaises(AllProvidersFailed) as caught:
            await router.complete(
                text_request("q"),
                lambda name, args: asyncio.sleep(0, result="unused"),
                operations=operations,
            )
        self.assertIn("timeout", str(caught.exception).lower())
        self.assertIsNotNone(provider.health.last_error)
