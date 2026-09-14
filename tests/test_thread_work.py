import asyncio
import threading
import unittest

from app.core.thread_work import run_bounded_thread


class BoundedThreadWorkTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_call_keeps_thread_capacity_until_sync_work_finishes(self):
        release = threading.Event()
        lock = threading.Lock()
        started = 0

        def blocked():
            nonlocal started
            with lock:
                started += 1
            release.wait(1)
            return 'done'

        tasks = [asyncio.create_task(run_bounded_thread(blocked)) for _ in range(4)]
        for _ in range(100):
            with lock:
                if started == 4:
                    break
            await asyncio.sleep(.005)
        self.assertEqual(started, 4)

        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        fifth = asyncio.create_task(run_bounded_thread(blocked))
        await asyncio.sleep(.02)
        with lock:
            self.assertEqual(started, 4)

        release.set()
        self.assertEqual(await fifth, 'done')
        with lock:
            self.assertEqual(started, 5)
