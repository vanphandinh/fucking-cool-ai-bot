"""Cooperative consent clock; only active operations spend an interval."""
from __future__ import annotations

import asyncio
import math
import time
from typing import Callable

TERMINAL = frozenset({'COMPLETED', 'CANCELLED', 'FAILED'})


class JobStopped(asyncio.CancelledError):
    """A user cancellation must never be classified as a provider failure."""


class JobControl:
    def __init__(self, *, renewal_sec: float, clock: Callable[[], float] = time.monotonic):
        if not math.isfinite(renewal_sec) or renewal_sec <= 0:
            raise ValueError('renewal_sec must be positive and finite')
        self.clock = clock
        self.renewal_sec = renewal_sec
        self.remaining = renewal_sec
        self.created_at = clock()
        self.state = 'QUEUED'
        self.generation = 0
        self.renewals = 0
        self.active_operations = 0
        self.active_duration = 0.0
        self._started: float | None = None
        self._lock = asyncio.Lock()
        self._permission = asyncio.Event()
        self._permission.set()

    def _account(self):
        if self._started is not None:
            elapsed = max(0, self.clock() - self._started)
            self.active_duration += elapsed
            self.remaining -= elapsed
            self._started = self.clock()

    def _expire(self):
        self._account()
        if self.state == 'RUNNING' and self.remaining <= 0:
            self.state = 'AWAITING_CONSENT'
            self.generation += 1
            self._permission.clear()
            return True
        return False

    async def expire_if_due(self) -> bool:
        async with self._lock:
            return self._expire()

    async def checkpoint(self) -> None:
        while True:
            async with self._lock:
                self._expire()
                if self.state in TERMINAL or self.state == 'DELIVERING':
                    raise JobStopped()
                if self.state != 'AWAITING_CONSENT':
                    return
            await self._permission.wait()

    async def activate(self) -> bool:
        async with self._lock:
            self._expire()
            if self.state in TERMINAL or self.state == 'DELIVERING':
                raise JobStopped()
            if self.state == 'AWAITING_CONSENT':
                return False
            if self.active_operations == 0:
                self._started = self.clock()
            self.active_operations += 1
            self.state = 'RUNNING'
            return True

    async def release_operation(self) -> None:
        async with self._lock:
            self._expire()
            self.active_operations = max(0, self.active_operations - 1)
            if not self.active_operations:
                self._started = None
                if self.state == 'RUNNING':
                    self.state = 'QUEUED'

    async def renew(self, generation: int) -> bool:
        async with self._lock:
            if self.state != 'AWAITING_CONSENT' or self.generation != generation:
                return False
            self._account()
            self.remaining = self.renewal_sec
            self.renewals += 1
            self.generation += 1
            self.state = 'RUNNING' if self.active_operations else 'QUEUED'
            self._permission.set()
            return True

    async def stop(self) -> bool:
        async with self._lock:
            if self.state in TERMINAL or self.state == 'DELIVERING':
                return False
            self.state = 'CANCELLED'
            self._permission.set()
            return True

    async def begin_delivery(self) -> bool:
        async with self._lock:
            if self.state in TERMINAL or self.state == 'DELIVERING':
                return False
            self.state = 'DELIVERING'
            self._permission.set()
            return True

    async def finish(self, state: str) -> None:
        if state not in TERMINAL:
            raise ValueError(state)
        async with self._lock:
            self.state = state
            self._permission.set()
