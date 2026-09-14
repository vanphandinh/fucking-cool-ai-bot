import asyncio
import unittest
from unittest.mock import patch

from app.core.job_control import JobControl, JobStopped


class ControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_ten_renewals_preserve_waiter_and_reject_duplicates(self):
        now = [0.0]
        c = JobControl(renewal_sec=180, clock=lambda: now[0])
        for _ in range(10):
            self.assertTrue(await c.activate())
            now[0] += 181
            self.assertTrue(await c.expire_if_due())
            self.assertFalse(await c.expire_if_due())
            waiter = asyncio.create_task(c.checkpoint())
            await asyncio.sleep(0)
            self.assertFalse(waiter.done())
            generation = c.generation
            self.assertTrue(await c.renew(generation))
            self.assertFalse(await c.renew(generation))
            await waiter
            await c.release_operation()
        self.assertEqual(c.renewals, 10)

    async def test_queue_does_not_spend_grant(self):
        now = [0.0]
        c = JobControl(renewal_sec=10, clock=lambda: now[0])
        now[0] = 100
        self.assertFalse(await c.expire_if_due())
        await c.activate()
        now[0] += 4
        await c.release_operation()
        now[0] += 100
        self.assertFalse(await c.expire_if_due())
        await c.activate()
        now[0] += 7
        self.assertTrue(await c.expire_if_due())

    async def test_release_operation_reports_expiry(self):
        now = [0.0]
        c = JobControl(renewal_sec=1, clock=lambda: now[0])
        self.assertTrue(await c.activate())
        now[0] = 2.0

        expired = await c.release_operation()

        self.assertTrue(expired)
        self.assertEqual(c.state, "AWAITING_CONSENT")
        self.assertEqual(c.active_operations, 0)

    async def test_abandoned_consent_can_time_out_and_renewal_clears_deadline(self):
        now = [0.0]
        c = JobControl(renewal_sec=1, clock=lambda: now[0])
        self.assertTrue(await c.activate())
        now[0] = 2.0
        self.assertTrue(await c.expire_if_due())
        self.assertEqual(c.consent_requested_at, 2.0)
        self.assertFalse(await c.consent_timed_out(10))

        now[0] = 12.1
        self.assertTrue(await c.consent_timed_out(10))
        generation = c.generation
        self.assertTrue(await c.renew(generation))
        self.assertIsNone(c.consent_requested_at)

    async def test_renewal_winning_timeout_race_is_not_cancelled(self):
        now = [0.0]
        c = JobControl(renewal_sec=1, clock=lambda: now[0], consent_timeout_sec=10)
        self.assertTrue(await c.activate())
        now[0] = 2.0
        self.assertTrue(await c.expire_if_due())

        async def timeout_after_renew(waiter, *, timeout):
            waiter.close()
            self.assertTrue(await c.renew(c.generation))
            raise TimeoutError

        with patch("app.core.job_control.asyncio.wait_for", side_effect=timeout_after_renew):
            await c.checkpoint()

        self.assertNotEqual(c.state, "CANCELLED")
        self.assertEqual(c.renewals, 1)

    async def test_stale_timeout_cannot_cancel_new_consent_generation(self):
        now = [0.0]
        c = JobControl(renewal_sec=1, clock=lambda: now[0], consent_timeout_sec=10)
        self.assertTrue(await c.activate())
        now[0] = 2.0
        self.assertTrue(await c.expire_if_due())
        calls = 0

        async def race_timeout(waiter, *, timeout):
            nonlocal calls
            calls += 1
            waiter.close()
            if calls == 1:
                self.assertTrue(await c.renew(c.generation))
                now[0] = 4.0
                self.assertTrue(await c.expire_if_due())
                raise TimeoutError
            self.assertTrue(await c.renew(c.generation))
            return None

        with patch("app.core.job_control.asyncio.wait_for", side_effect=race_timeout):
            await c.checkpoint()

        self.assertNotEqual(c.state, "CANCELLED")
        self.assertEqual(c.renewals, 2)

    async def test_stop_wakes_waiter_and_prevents_delivery(self):
        c = JobControl(renewal_sec=1, clock=lambda: 0)
        self.assertTrue(await c.stop())
        with self.assertRaises(JobStopped):
            await c.checkpoint()
        self.assertFalse(await c.begin_delivery())

    async def test_delivery_can_win_after_expiry(self):
        now = [0.0]
        c = JobControl(renewal_sec=1, clock=lambda: now[0])
        await c.activate()
        now[0] = 2
        await c.expire_if_due()
        self.assertTrue(await c.begin_delivery())
        self.assertFalse(await c.stop())
        self.assertFalse(await c.renew(c.generation))

    async def test_invalid_interval(self):
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                JobControl(renewal_sec=value)
