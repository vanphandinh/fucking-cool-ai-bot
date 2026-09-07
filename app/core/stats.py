"""Thống kê vận hành đơn giản (phục vụ /status)."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime


class Stats:
    def __init__(self) -> None:
        self.started_at = datetime.now()
        self.questions_total = 0
        self.questions_today = 0
        self.searches = 0
        self._day = date.today()
        self.by_provider: Counter[str] = Counter()
        self.last_provider: str | None = None
        self.last_error: str | None = None
        self.fallback_count = 0

    def record_question(self) -> None:
        self._roll_day()
        self.questions_total += 1
        self.questions_today += 1

    def record_answer(self, provider: str) -> None:
        self._roll_day()
        self.by_provider[provider] += 1
        self.last_provider = provider

    def record_search(self) -> None:
        self.searches += 1

    def record_error(self, message: str, *, fallback: bool = False) -> None:
        self.last_error = (message or "")[:300]
        if fallback:
            self.fallback_count += 1

    def _roll_day(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self.questions_today = 0

    def uptime_text(self) -> str:
        delta = datetime.now() - self.started_at
        hours, rem = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours}h {minutes}m {seconds}s"
