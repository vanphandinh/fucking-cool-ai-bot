import asyncio
import unittest

from app.core.job_control import JobControl
from app.core.job_operations import OperationRunner, OperationBudget


class OperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_expiry_finishes_current_but_parks_fallback_without_slot(self):
        now = [0.0]
        c = JobControl(renewal_sec=1, clock=lambda: now[0])
        slots = asyncio.Semaphore(1)
        r = OperationRunner(c, slots, lambda e: None)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def slow():
            entered.set()
            await release.wait()
            return 'evidence'
        first = asyncio.create_task(r.run('URL', slow, timeout_sec=1))
        await entered.wait()
        now[0] = 2
        await c.expire_if_due()
        release.set()
        self.assertEqual(await first, 'evidence')
        async def second():
            calls.append(1)
            return 'next'
        task = asyncio.create_task(r.run('fallback', second, timeout_sec=.01))
        await asyncio.sleep(.02)
        self.assertEqual(calls, [])
        self.assertEqual(slots._value, 1)
        await c.renew(c.generation)
        self.assertEqual(await task, 'next')

    async def test_global_capacity_and_cancellation(self):
        slots = asyncio.Semaphore(1)
        entered = asyncio.Event()
        async def blocked():
            entered.set()
            await asyncio.Event().wait()
        r1 = OperationRunner(JobControl(renewal_sec=10), slots, lambda e: None)
        r2 = OperationRunner(JobControl(renewal_sec=10), slots, lambda e: None)
        a = asyncio.create_task(r1.run('a', blocked, timeout_sec=1))
        await entered.wait()
        b = asyncio.create_task(r2.run('b', blocked, timeout_sec=1))
        await asyncio.sleep(0)
        self.assertEqual(r2.control.active_operations, 0)
        a.cancel()
        b.cancel()
        await asyncio.gather(a, b, return_exceptions=True)
        self.assertEqual(slots._value, 1)

    async def test_timeout_spends_budget_but_queue_does_not(self):
        r = OperationRunner(JobControl(renewal_sec=10), asyncio.Semaphore(1), lambda e: None)
        budget = OperationBudget(.01)
        async def blocked():
            await asyncio.Event().wait()
        with self.assertRaises(TimeoutError):
            await r.run('read', blocked, timeout_sec=1, budget=budget)
        self.assertLessEqual(budget.remaining, 0)
        self.assertEqual(r.control.active_operations, 0)

    async def test_reader_fallback_waits_for_consent(self):
        from unittest.mock import patch
        import httpx
        from app.search import reader
        now = [0.0]
        c = JobControl(renewal_sec=1, clock=lambda: now[0])
        r = OperationRunner(c, asyncio.Semaphore(1), lambda e: None)
        urls = []
        async def fetch(client, url):
            urls.append(url)
            if len(urls) == 1:
                now[0] = 2
                raise httpx.ConnectError('offline')
            return reader._FetchResult(b'<p>evidence</p>', 'utf-8')
        with patch.object(reader, '_fetch_limited', side_effect=fetch):
            task = asyncio.create_task(reader.read_page('https://example.com', operations=r))
            await asyncio.sleep(.01)
            self.assertEqual(len(urls), 1)
            await c.renew(c.generation)
            self.assertIn('evidence', await task)

    async def test_x_thread_fallback_keeps_incomplete_thread_notice(self):
        from unittest.mock import AsyncMock, patch
        from app.search import x_reader
        target = x_reader.parse_x_status_url('https://x.com/alice/status/1234')
        c = JobControl(renewal_sec=10)
        r = OperationRunner(c, asyncio.Semaphore(1), lambda e: None)
        focal = x_reader.XReadResult('focal', 'fxtwitter', target.canonical_url)
        with patch.object(x_reader, '_fetch_fxtwitter',
                          new=AsyncMock(side_effect=[x_reader.XFetchError(), focal])):
            result = await x_reader.read_x_url(target, 'x_thread', 10, operations=r)
        self.assertIn('Full thread unavailable', result.text)
        self.assertFalse(result.thread_complete)
