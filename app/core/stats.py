"""Thống kê vận hành đơn giản (phục vụ /status)."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone

_VN_TZ = timezone(timedelta(hours=7))


def _today_vn() -> date:
    return datetime.now(_VN_TZ).date()


class Stats:
    def __init__(self) -> None:
        self.started_at = datetime.now()
        self.questions_total = 0
        self.questions_today = 0
        self.searches = 0
        self._day = _today_vn()
        self.by_provider: Counter[str] = Counter()
        self.last_provider: str | None = None
        self.last_error: str | None = None
        self.fallback_count = 0

    def live_questions_today(self) -> int:
        self._roll_day()
        return self.questions_today

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

    def record_fallback(self, count: int = 1) -> None:
        self.fallback_count += max(0, count)

    def record_error(self, message: str, *, fallback: bool = False) -> None:
        self.last_error = (message or "")[:300]
        if fallback:
            self.fallback_count += 1

    def _roll_day(self) -> None:
        today = _today_vn()
        if today != self._day:
            self._day = today
            self.questions_today = 0

    def uptime_text(self) -> str:
        delta = datetime.now() - self.started_at
        hours, rem = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{hours}h {minutes}m {seconds}s"
