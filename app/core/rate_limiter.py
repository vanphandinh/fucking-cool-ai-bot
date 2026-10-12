"""Rate limiter đơn giản trong RAM: chặn spam theo (user, phút)."""

from __future__ import annotations

import math
import time
from collections import deque
from heapq import heapify, heappop, heappush
from itertools import count
from typing import Callable


class RateLimiter:
    # Retain warnings long enough for any supported cooldown (0–120 seconds),
    # even if a caller increases its cooldown after an earlier warning.
    _WARNING_LOOKBACK_SEC = 120.0

    def __init__(
        self,
        max_requests_per_min: int = 3,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_requests_per_min
        self._window = 60.0
        self._clock = clock
        self._stamps: dict[int, deque[float]] = {}
        self._last_warn: dict[int, float] = {}
        # Each active user has one deadline per independent kind of state.
        # Tokens distinguish superseded heap entries (including equal deadlines).
        self._request_expirations: dict[int, tuple[float, int]] = {}
        self._warning_expirations: dict[int, tuple[float, int]] = {}
        self._expiry_heap: list[tuple[float, int, str, int]] = []
        self._expiry_tokens = count()

    def _compact_heap(self) -> None:
        active = len(self._request_expirations) + len(self._warning_expirations)
        if len(self._expiry_heap) <= 2 * active + 64:
            return
        self._expiry_heap = [
            (deadline, token, "request", user_id)
            for user_id, (deadline, token) in self._request_expirations.items()
        ]
        self._expiry_heap.extend(
            (deadline, token, "warning", user_id)
            for user_id, (deadline, token) in self._warning_expirations.items()
        )
        heapify(self._expiry_heap)

    def _schedule(self, kind: str, user_id: int, deadline: float) -> None:
        token = next(self._expiry_tokens)
        expirations = (
            self._request_expirations if kind == "request" else self._warning_expirations
        )
        expirations[user_id] = (deadline, token)
        heappush(self._expiry_heap, (deadline, token, kind, user_id))
        self._compact_heap()

    def _expire(self, now: float) -> None:
        while self._expiry_heap and self._expiry_heap[0][0] <= now:
            deadline, token, kind, user_id = heappop(self._expiry_heap)
            expirations = (
                self._request_expirations if kind == "request" else self._warning_expirations
            )
            if expirations.get(user_id) != (deadline, token):
                continue  # Superseded by a newer accepted request or warning.
            expirations.pop(user_id)
            if kind == "request":
                self._stamps.pop(user_id, None)
            else:
                self._last_warn.pop(user_id, None)
        self._compact_heap()

    def allow(self, user_id: int) -> tuple[bool, float]:
        """Trả về (được phép?, số giây phải chờ)."""
        now = self._clock()
        self._expire(now)
        if self._max <= 0:
            # 0 = tắt hết câu hỏi; không được IndexError vì stamps rỗng.
            return False, self._window
        stamps = self._stamps.setdefault(user_id, deque())
        while stamps and now - stamps[0] >= self._window:
            stamps.popleft()
        if len(stamps) < self._max:
            stamps.append(now)
            # Only accepted requests extend retention. Denied spam does not.
            self._schedule("request", user_id, now + self._window)
            return True, 0.0
        oldest = stamps[0] if stamps else now
        retry_in = max(0.0, self._window - (now - oldest))
        return False, retry_in

    def should_warn(self, user_id: int, cooldown_sec: float = 30.0) -> bool:
        """Chỉ cảnh báo 1 lần mỗi cooldown hợp lệ từ 0 đến 120 giây."""
        if isinstance(cooldown_sec, bool) or not isinstance(cooldown_sec, (int, float)):
            raise TypeError("cooldown_sec must be an int or float (not bool)")
        if not 0 <= cooldown_sec <= self._WARNING_LOOKBACK_SEC or not math.isfinite(
            cooldown_sec
        ):
            raise ValueError("cooldown_sec must be finite and between 0 and 120")
        now = self._clock()
        self._expire(now)
        last = self._last_warn.get(user_id)
        if last is None or now - last >= cooldown_sec:
            self._last_warn[user_id] = now
            self._schedule("warning", user_id, now + self._WARNING_LOOKBACK_SEC)
            return True
        return False
