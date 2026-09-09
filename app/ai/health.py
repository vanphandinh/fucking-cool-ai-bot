"""In-memory provider health/cooldown state for quota-aware fallback."""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ProviderHealth:
    cooldown_until: float = 0.0
    disabled: bool = False
    consecutive_transient_failures: int = 0
    last_error: str | None = None

    def available(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return not self.disabled and current >= self.cooldown_until

    def record_success(self) -> None:
        self.consecutive_transient_failures = 0
        self.last_error = None
        self.cooldown_until = 0.0

    def record_error(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        transient: bool = True,
    ) -> None:
        self.last_error = (message or "")[:300]
        now = time.monotonic()
        if status_code in (401, 403):
            self.disabled = True
            return
        if status_code == 429:
            self.cooldown_until = now + max(0.0, retry_after if retry_after is not None else 60.0)
            return
        if transient:
            self.consecutive_transient_failures += 1
            if self.consecutive_transient_failures >= 2:
                self.cooldown_until = now + 30.0

    def cooldown_seconds(self) -> int:
        return max(0, int(self.cooldown_until - time.monotonic()))
