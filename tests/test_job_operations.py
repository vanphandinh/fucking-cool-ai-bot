import asyncio
import unittest

from app.core.job_control import JobControl
from app.core.job_operations import OperationBudget, OperationRunner


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
            return "evidence"

        first = asyncio.create_task(r.run("URL", slow, timeout_sec=1))
        await entered.wait()
        now[0] = 2
        await c.expire_if_due()
        release.set()
        self.assertEqual(await first, "evidence")

        async def second():
            calls.append(1)
            return "next"

        task = asyncio.create_task(r.run("fallback", second, timeout_sec=.01))
        await asyncio.sleep(.02)
        self.assertEqual(calls, [])
        self.assertEqual(slots._value, 1)
        await c.renew(c.generation)
        self.assertEqual(await task, "next")

    async def test_release_expiry_emits_renewal_requested(self):
        now = [0.0]
        events = []
        c = JobControl(renewal_sec=1, clock=lambda: now[0])
        r = OperationRunner(c, asyncio.Semaphore(1), events.append)

        async def work():
            now[0] = 2.0
            return "done"

        self.assertEqual(await r.run("leaf", work, timeout_sec=1), "done")
        self.assertEqual(c.state, "AWAITING_CONSENT")
        self.assertEqual(
            [event.kind for event in events],
            ["stage_started", "stage_finished", "renewal_requested"],
        )
        self.assertEqual(events[-1].label, "leaf")

    async def test_global_capacity_and_cancellation(self):
        slots = asyncio.Semaphore(1)
        entered = asyncio.Event()

        async def blocked():
            entered.set()
            await asyncio.Event().wait()

        r1 = OperationRunner(JobControl(renewal_sec=10), slots, lambda e: None)
        r2 = OperationRunner(JobControl(renewal_sec=10), slots, lambda e: None)
        a = asyncio.create_task(r1.run("a", blocked, timeout_sec=1))
        await entered.wait()
        b = asyncio.create_task(r2.run("b", blocked, timeout_sec=1))
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
            await r.run("read", blocked, timeout_sec=1, budget=budget)
        self.assertLessEqual(budget.remaining, 0)
        self.assertEqual(r.control.active_operations, 0)

    async def test_nested_context_budget_is_charged_once_per_attempt(self):
        r = OperationRunner(JobControl(renewal_sec=10), asyncio.Semaphore(1), lambda e: None)

        async def tiny():
            await asyncio.sleep(.005)
            return "ok"

        with r.budget(.05) as shared:
            before = shared.remaining
            self.assertEqual(
                await r.run("nested", tiny, timeout_sec=.1, budget=shared),
                "ok",
            )
            spent = before - shared.remaining

        self.assertGreater(spent, 0)
        self.assertLess(spent, .02)

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
                raise httpx.ConnectError("offline")
            return reader._FetchResult(b"<p>evidence</p>", "utf-8")

        with patch.object(reader, "_fetch_limited", side_effect=fetch):
            task = asyncio.create_task(reader.read_page("https://example.com", operations=r))
            await asyncio.sleep(.01)
            self.assertEqual(len(urls), 1)
            await c.renew(c.generation)
            self.assertIn("evidence", await task)

    async def test_x_thread_fallback_keeps_incomplete_thread_notice(self):
        from unittest.mock import AsyncMock, patch

        from app.search import x_reader

        target = x_reader.parse_x_status_url("https://x.com/alice/status/1234")
        c = JobControl(renewal_sec=10)
        r = OperationRunner(c, asyncio.Semaphore(1), lambda e: None)
        focal = x_reader.XReadResult("focal", "fxtwitter", target.canonical_url)
        with patch.object(
            x_reader,
            "_fetch_fxtwitter",
            new=AsyncMock(side_effect=[x_reader.XFetchError(), focal]),
        ):
            result = await x_reader.read_x_url(target, "x_thread", 10, operations=r)
        self.assertIn("Full thread unavailable", result.text)
        self.assertFalse(result.thread_complete)

    async def test_progress_emit_failure_does_not_break_operation(self):
        slots = asyncio.Semaphore(1)

        def broken_emit(event):
            raise RuntimeError("presenter offline")

        runner = OperationRunner(JobControl(renewal_sec=10), slots, broken_emit)

        async def work():
            return "ok"

        self.assertEqual(await runner.run("backend", work, timeout_sec=.1), "ok")
        self.assertEqual(slots._value, 1)
        self.assertEqual(runner.control.active_operations, 0)

    async def test_controlled_search_pause_does_not_spend_total_budget(self):
        import httpx

        from app.config import Settings
        from app.search.router import route_auto

        now = [0.0]
        control = JobControl(renewal_sec=1, clock=lambda: now[0])
        runner = OperationRunner(control, asyncio.Semaphore(1), lambda event: None)
        calls = []

        async def searx(query, settings, limit):
            calls.append("searxng")
            now[0] = 2.0
            await control.expire_if_due()
            raise httpx.ConnectError("offline")

        async def ddgs(query, settings, limit):
            calls.append("ddgs")
            return [{"title": "ok", "url": "https://example.com", "snippet": ""}]

        settings = Settings(
            search_backend="auto",
            searxng_url="http://searxng:8080",
            searxng_timeout_sec=.01,
            ddgs_timeout_sec=.01,
            search_total_timeout_sec=.02,
        )
        task = asyncio.create_task(
            route_auto(
                "renewable jobs",
                settings,
                4,
                kind="web",
                result_url_key="url",
                searx_call=searx,
                ddgs_call=ddgs,
                operations=runner,
            )
        )
        await asyncio.sleep(.04)
        self.assertEqual(calls, ["searxng"])
        self.assertEqual(control.state, "AWAITING_CONSENT")
        await control.renew(control.generation)
        result = await task
        self.assertEqual(calls, ["searxng", "ddgs"])
        self.assertEqual(result[0]["url"], "https://example.com")
