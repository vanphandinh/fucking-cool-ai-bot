"""Bound synchronous thread work without pretending cancellation stops the thread."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from weakref import WeakKeyDictionary

_MAX_WORKERS = 4
_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="bot-blocking")
_loop_slots: WeakKeyDictionary = WeakKeyDictionary()


def _slots_for(loop: asyncio.AbstractEventLoop) -> asyncio.Semaphore:
    slots = _loop_slots.get(loop)
    if slots is None:
        slots = asyncio.Semaphore(_MAX_WORKERS)
        _loop_slots[loop] = slots
    return slots


async def run_bounded_thread(factory):
    """Run one sync call while capacity tracks the real underlying thread lifetime.

    Cancelling the asyncio waiter does not release the limiter early. The slot is
    returned only by the concurrent future's done callback, after the sync call
    has actually exited.
    """
    loop = asyncio.get_running_loop()
    slots = _slots_for(loop)
    await slots.acquire()
    try:
        future = _executor.submit(factory)
    except BaseException:
        slots.release()
        raise

    def release_slot(_future) -> None:
        try:
            loop.call_soon_threadsafe(slots.release)
        except RuntimeError:
            pass

    future.add_done_callback(release_slot)
    return await asyncio.shield(asyncio.wrap_future(future, loop=loop))
