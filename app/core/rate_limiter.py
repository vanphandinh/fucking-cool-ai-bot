"""Rate limiter đơn giản trong RAM: chặn spam theo (user, phút)."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_requests_per_min: int = 3) -> None:
        self._max = max_requests_per_min
        self._window = 60.0
        self._stamps: dict[int, deque[float]] = defaultdict(deque)
        self._last_warn: dict[int, float] = {}

    def allow(self, user_id: int) -> tuple[bool, float]:
        """Trả về (được phép?, số giây phải chờ)."""
        now = time.monotonic()
        if self._max <= 0:
            # 0 = tắt hết câu hỏi; không được IndexError vì stamps rỗng.
            return False, self._window
        stamps = self._stamps[user_id]
        while stamps and now - stamps[0] > self._window:
            stamps.popleft()
        if len(stamps) < self._max:
            stamps.append(now)
            return True, 0.0
        oldest = stamps[0] if stamps else now
        retry_in = max(0.0, self._window - (now - oldest))
        return False, retry_in

    def should_warn(self, user_id: int, cooldown_sec: float = 30.0) -> bool:
        """Chỉ cảnh báo 1 lần mỗi cooldown để tránh spam thông báo."""
        now = time.monotonic()
        last = self._last_warn.get(user_id, 0.0)
        if now - last >= cooldown_sec:
            self._last_warn[user_id] = now
            return True
        return False
