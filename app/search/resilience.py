"""Small in-process resilience primitives for web and image search."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, TypeVar


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class FailureKind(str, Enum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class BackendKey:
    namespace: int
    backend: str
    kind: str


@dataclass
class _CircuitEntry:
    state: CircuitState = CircuitState.CLOSED
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    probe_in_flight: bool = False
    last_failure_reason: str = ""


class CircuitBreaker:
    def __init__(
        self,
        *,
        failure_threshold: int,
        cooldown_sec: float,
        rate_limit_cooldown_sec: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_sec = cooldown_sec
        self.rate_limit_cooldown_sec = rate_limit_cooldown_sec
        self._clock = clock
        self._entries: dict[BackendKey, _CircuitEntry] = {}

    def _entry(self, key: BackendKey) -> _CircuitEntry:
        return self._entries.setdefault(key, _CircuitEntry())

    def state(self, key: BackendKey) -> CircuitState:
        return self._entry(key).state

    def allow_request(self, key: BackendKey) -> bool:
        entry = self._entry(key)
        if entry.state is CircuitState.CLOSED:
            return True
        if entry.state is CircuitState.OPEN:
            if self._clock() < entry.cooldown_until:
                return False
            entry.state = CircuitState.HALF_OPEN
            entry.probe_in_flight = True
            return True
        if entry.probe_in_flight:
            return False
        entry.probe_in_flight = True
        return True

    def record_success(self, key: BackendKey) -> None:
        entry = self._entry(key)
        entry.state = CircuitState.CLOSED
        entry.consecutive_failures = 0
        entry.cooldown_until = 0.0
        entry.probe_in_flight = False
        entry.last_failure_reason = ""

    def record_cancelled(self, key: BackendKey) -> None:
        entry = self._entry(key)
        if entry.state is CircuitState.HALF_OPEN:
            entry.probe_in_flight = False

    def record_failure(self, key: BackendKey, kind: FailureKind, reason: str = "") -> None:
        entry = self._entry(key)
        was_half_open = entry.state is CircuitState.HALF_OPEN
        entry.probe_in_flight = False
        entry.last_failure_reason = reason[:200]
        entry.consecutive_failures += 1

        should_open = kind is FailureKind.RATE_LIMIT
        should_open = should_open or was_half_open
        should_open = should_open or entry.consecutive_failures >= self.failure_threshold
        if not should_open:
            return

        if kind is FailureKind.RATE_LIMIT:
            cooldown = self.rate_limit_cooldown_sec
        else:
            cooldown = self.cooldown_sec
        entry.state = CircuitState.OPEN
        entry.cooldown_until = self._clock() + cooldown


@dataclass
class _CacheEntry:
    value: list[dict]
    fresh_until: float
    stale_until: float


class SearchCache:
    def __init__(
        self,
        *,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()

    def set(
        self,
        key: str,
        value: list[dict],
        *,
        fresh_ttl_sec: float,
        stale_ttl_sec: float,
    ) -> None:
        if not value:
            return
        now = self._clock()
        self._entries[key] = _CacheEntry(
            value=[dict(item) for item in value],
            fresh_until=now + fresh_ttl_sec,
            stale_until=now + stale_ttl_sec,
        )
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def get_fresh(self, key: str) -> list[dict] | None:
        entry = self._entries.get(key)
        if entry is None or self._clock() >= entry.fresh_until:
            return None
        self._entries.move_to_end(key)
        return [dict(item) for item in entry.value]

    def get_stale(self, key: str) -> list[dict] | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._clock() >= entry.stale_until:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return [dict(item) for item in entry.value]


T = TypeVar("T")


@dataclass
class _Flight:
    task: asyncio.Task
    waiters: int = 0


class SingleFlight:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._flights: dict[str, _Flight] = {}

    async def run(self, key: str, factory: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            flight = self._flights.get(key)
            if flight is None:
                flight = _Flight(task=asyncio.create_task(factory()))
                self._flights[key] = flight
            flight.waiters += 1

        try:
            return await asyncio.shield(flight.task)
        finally:
            async with self._lock:
                current = self._flights.get(key)
                if current is flight:
                    flight.waiters -= 1
                    if flight.waiters == 0:
                        self._flights.pop(key, None)
                        if not flight.task.done():
                            flight.task.cancel()
