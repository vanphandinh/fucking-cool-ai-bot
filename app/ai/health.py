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
    generation: int = 0

    def available(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return not self.disabled and current >= self.cooldown_until

    def record_success(self) -> None:
        self.generation += 1
        self.consecutive_transient_failures = 0
        self.last_error = None
        self.cooldown_until = 0.0

    def record_disabled(self, message: str) -> None:
        """Explicitly disable this health cell without inventing an HTTP status."""

        self.generation += 1
        self.last_error = (message or "")[:300]
        self.disabled = True

    def record_cooldown(
        self,
        message: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        """Explicitly cool down this health cell without inventing an HTTP status."""

        self.generation += 1
        self.last_error = (message or "")[:300]
        self.cooldown_until = time.monotonic() + max(
            0.0, retry_after if retry_after is not None else 60.0
        )

    def record_transient_error(self, message: str) -> None:
        """Record a generic transient health failure using existing bounded semantics."""

        self.generation += 1
        self._apply_error(
            message,
            status_code=None,
            retry_after=None,
            transient=True,
        )

    def record_observation(self, message: str) -> None:
        """Record diagnostic state without changing availability."""

        self.generation += 1
        self._apply_error(
            message,
            status_code=None,
            retry_after=None,
            transient=False,
        )

    def record_error(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        transient: bool = True,
    ) -> None:
        self.generation += 1
        self._apply_error(
            message,
            status_code=status_code,
            retry_after=retry_after,
            transient=transient,
        )

    def record_deferred_error(
        self,
        message: str,
        *,
        expected_generation: int,
        status_code: int | None = None,
        retry_after: float | None = None,
        transient: bool = True,
    ) -> bool:
        if self.generation != expected_generation:
            return False
        self._apply_error(
            message,
            status_code=status_code,
            retry_after=retry_after,
            transient=transient,
        )
        return True

    def _apply_error(
        self,
        message: str,
        *,
        status_code: int | None,
        retry_after: float | None,
        transient: bool,
    ) -> None:
        self.last_error = (message or "")[:300]
        now = time.monotonic()
        if status_code in (401, 403):
            self.disabled = True
            return
        if status_code == 429:
            self.cooldown_until = now + max(
                0.0, retry_after if retry_after is not None else 60.0
            )
            return
        if transient:
            self.consecutive_transient_failures += 1
            if self.consecutive_transient_failures >= 2:
                self.cooldown_until = now + 30.0

    def cooldown_seconds(self) -> int:
        return max(0, int(self.cooldown_until - time.monotonic()))
