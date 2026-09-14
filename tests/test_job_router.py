import asyncio
import unittest

from app.ai.base import ChatResponse, ToolCall
from app.ai.router import AIProviderRouter
from app.core.job_control import JobControl
from app.core.job_operations import OperationRunner
from tests.provider_fakes import ScriptedProvider, fetch_url_tool


class RenewableRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_response_after_expiry_is_delivered_without_renew(self):
        now = [0.0]
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowProvider(ScriptedProvider):
            async def chat(self, messages, tools=None):
                entered.set()
                await release.wait()
                return ChatResponse(content='done')

        provider = SlowProvider('one', [])
        router = AIProviderRouter([provider])
        control = JobControl(renewal_sec=1, clock=lambda: now[0])
        operations = OperationRunner(control, asyncio.Semaphore(1), lambda event: None)
        task = asyncio.create_task(
            router.complete(
                [{'role': 'user', 'content': 'q'}],
                None,
                lambda name, args: asyncio.sleep(0, result='unused'),
                operations=operations,
            )
        )
        await entered.wait()
        now[0] = 2
        await control.expire_if_due()
        release.set()
        result = await asyncio.wait_for(task, .2)
        self.assertEqual(result.content, 'done')
        self.assertEqual(control.state, 'AWAITING_CONSENT')

    async def test_tool_call_after_expiry_waits_before_dispatch_and_resumes_same_state(self):
        now = [0.0]
        entered = asyncio.Event()
        release = asyncio.Event()
        tool_calls = []

        class SlowProvider(ScriptedProvider):
            def __init__(self):
                super().__init__('one', [])
                self.turn = 0

            async def chat(self, messages, tools=None):
                self.turn += 1
                if self.turn == 1:
                    entered.set()
                    await release.wait()
                    return ChatResponse(
                        tool_calls=[ToolCall(id='t1', name='fetch_url', arguments={'url': 'https://a'})]
                    )
                return ChatResponse(content='answer')

        async def tool(name, args):
            tool_calls.append((name, args['url']))
            return 'evidence'

        provider = SlowProvider()
        router = AIProviderRouter([provider])
        control = JobControl(renewal_sec=1, clock=lambda: now[0])
        operations = OperationRunner(control, asyncio.Semaphore(1), lambda event: None)
        task = asyncio.create_task(
            router.complete(
                [{'role': 'user', 'content': 'q'}],
                [fetch_url_tool()],
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
        self.assertEqual(result.content, 'answer')
        self.assertEqual(tool_calls, [('fetch_url', 'https://a')])
        self.assertEqual(provider.turn, 2)

    async def test_local_ai_timeout_is_classified_retryable_read_timeout(self):
        from app.ai.base import AllProvidersFailed

        async def forever(_name, _args):
            await asyncio.Event().wait()

        class HungProvider(ScriptedProvider):
            async def chat(self, messages, tools=None):
                await asyncio.Event().wait()

        provider = HungProvider('hung', [])
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
                [{'role': 'user', 'content': 'q'}],
                None,
                forever,
                operations=operations,
            )
        self.assertIn('timeout', str(caught.exception).lower())
        self.assertGreaterEqual(provider.health.failures, 1)
