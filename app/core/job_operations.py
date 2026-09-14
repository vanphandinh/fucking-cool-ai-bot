"""Gate each leaf attempt and exclude queue/consent from local budgets."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from .job_progress import ProgressEvent

logger = logging.getLogger(__name__)


@dataclass
class OperationBudget:
    remaining: float


_budgets: ContextVar[tuple[OperationBudget, ...]] = ContextVar('job_budgets', default=())


class OperationRunner:
    def __init__(self, control, slots, emit, *, ai_timeout=90, tool_timeout=45):
        self.control = control
        self.slots = slots
        self.emit = emit
        self.ai_timeout = ai_timeout
        self.tool_timeout = tool_timeout

    @contextmanager
    def budget(self, seconds):
        token = _budgets.set((*_budgets.get(), OperationBudget(seconds)))
        try:
            yield
        finally:
            _budgets.reset(token)

    def _emit(self, event: ProgressEvent) -> None:
        try:
            self.emit(event)
        except Exception:  # noqa: BLE001 - presenter failures must not break cleanup/work
            logger.debug("Progress callback failed", exc_info=True)

    async def run(self, label, factory, *, timeout_sec, budget=None):
        budgets = (*_budgets.get(), *((budget,) if budget is not None else ()))
        while True:
            await self.control.checkpoint()
            await self.slots.acquire()
            active = False
            started = None
            ok = False
            try:
                active = await self.control.activate()
                if not active:
                    continue
                timeout = (
                    min(timeout_sec, *(b.remaining for b in budgets)) if budgets else timeout_sec
                )
                if timeout <= 0:
                    raise TimeoutError('operation budget exhausted')
                self._emit(ProgressEvent('stage_started', label))
                started = time.monotonic()
                async with asyncio.timeout(timeout):
                    result = await factory()
                ok = True
                return result
            finally:
                if started is not None:
                    elapsed = time.monotonic() - started
                    for b in budgets:
                        b.remaining -= elapsed
                    self._emit(ProgressEvent('stage_finished', label, ok))
                if active:
                    await self.control.release_operation()
                self.slots.release()
