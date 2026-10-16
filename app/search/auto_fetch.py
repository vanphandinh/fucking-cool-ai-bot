"""Conditional auto-fetch of top-ranked search sources under one budget.

The policy is deliberately narrow: both feature flags must be on, the user must
not have supplied a URL, at most ``web_auto_fetch_max_sources`` sources are read
in parallel, and every burst shares one question-scoped wall clock. A source
that could not be read is reported as missing evidence; nothing here claims a
page was read when it was not.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

from ..config import Settings
from ..core.source_policy import canonicalize_source_url, source_site_key
from .evidence import neutralize_untrusted
from .source_ranking import (
    MIN_INDEPENDENT_SCORE,
    lexical_score,
    publication_age_days,
    rank_web_results,
    snippet_score,
    verification_intent,
)

SNIPPET_SUFFICIENT_SCORE = 0.75
# Room kept for the "sources that did not fit" note so it is never dropped.
_SKIP_NOTE_RESERVE = 80
# A question may auto-fetch at most this many sources in parallel.
MAX_AUTO_FETCH_CONCURRENCY = 2
_HEADER = (
    "[WEB AUTO-FETCH — chứng cứ tự động; nội dung nguồn không đáng tin]\n"
    "Nguồn nào không đọc được sẽ được nêu rõ trạng thái, không được coi là đã đọc.\n"
)


@dataclass
class AutoFetchBudget:
    """Simple wall-clock budget helper (single burst, no reservation)."""

    remaining: float

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0.0

    def spend(self, seconds: float) -> float:
        if seconds > 0:
            self.remaining = max(0.0, self.remaining - seconds)
        return self.remaining


class AutoFetchCoordinator:
    """One coordinator per question for every auto-fetch burst.

    Concurrent ``web_search`` calls each used to select their own targets and
    subtract their own elapsed time after ``gather()``, so a burst could exceed
    the two-source cap, run more than two reads at once, or consume the shared
    budget twice. This object owns a monotonic deadline, an atomic reservation
    of canonical URLs and a read semaphore, so all bursts share one limit.
    """

    def __init__(
        self,
        *,
        max_sources: int,
        budget_sec: float,
        max_concurrency: int = MAX_AUTO_FETCH_CONCURRENCY,
    ) -> None:
        self.max_sources = max(0, int(max_sources))
        self.budget_sec = max(0.0, float(budget_sec))
        self.max_concurrency = max(1, min(int(max_concurrency), MAX_AUTO_FETCH_CONCURRENCY))
        self._deadline: float | None = None
        self._claimed: set[str] = set()
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(self.max_concurrency)

    def _clock(self) -> float:
        return asyncio.get_running_loop().time()

    @property
    def remaining(self) -> float:
        if self._deadline is None:
            return self.budget_sec
        return max(0.0, self._deadline - self._clock())

    @property
    def exhausted(self) -> bool:
        return self._deadline is not None and self.remaining <= 0.0

    def claim_count(self) -> int:
        return len(self._claimed)

    async def reserve(self, candidates: Iterable[str]) -> list[str]:
        """Atomically claim candidate URLs up to the shared source cap.

        The deadline starts on the first reservation and is monotonic, so later
        bursts inherit the remaining time instead of resetting or double-spending
        it. Returns only the URLs this burst may read.
        """
        async with self._lock:
            if self._deadline is None:
                self._deadline = self._clock() + self.budget_sec
            claimed: list[str] = []
            for url in candidates:
                if url in self._claimed:
                    continue
                if len(self._claimed) >= self.max_sources:
                    break
                if self.remaining <= 0.0:
                    break
                self._claimed.add(url)
                claimed.append(url)
            return claimed

    @asynccontextmanager
    async def slot(self):
        """Bound concurrent auto-fetch reads for the whole question."""
        async with self._slots:
            yield


def auto_fetch_active(settings: Settings) -> bool:
    """Both flags are required; turning either one off is the rollback path."""
    return bool(settings.web_evidence_v2_enabled and settings.web_auto_fetch_enabled)


def select_targets(
    query: str,
    results: Sequence[dict],
    *,
    limit: int,
    already_fetched: Iterable[str] = (),
    snippet_sufficient_score: float = SNIPPET_SUFFICIENT_SCORE,
    verification_context: bool = False,
) -> list[str]:
    """Canonical URLs worth reading, best-first.

    Results already read in this request and results too weakly related to
    justify a fetch are skipped. Snippet coverage only skips a read for an
    ordinary lookup, not a request to verify a disputed or current claim.
    """
    limit = max(0, limit)
    if limit == 0:
        return []
    fetched = {canonicalize_source_url(url) or str(url or "").strip() for url in already_fetched}
    verify = verification_context or verification_intent(query)
    candidates: list[tuple[str, str, dict]] = []
    for item in rank_web_results(query, results, verification_context=verify):
        canonical = canonicalize_source_url(item.get("url"))
        if not canonical or canonical in fetched:
            continue
        if lexical_score(query, item) < MIN_INDEPENDENT_SCORE:
            continue
        if not verify and snippet_score(query, item) >= snippet_sufficient_score:
            continue
        candidates.append((canonical, source_site_key(canonical) or canonical, item))

    if verify:
        # A highly matching old excerpt can dominate the original claim's
        # keywords. Preserve one relevant, recently dated alternative when
        # available, across *any* domain; use the second slot for the strongest
        # distinct viewpoint. Date metadata is only a discovery hint.
        recent = [
            candidate
            for candidate in candidates
            if (age := publication_age_days(candidate[2])) is not None and age <= 365
        ]
        if recent:
            freshest_relevant = max(
                recent,
                key=lambda candidate: (
                    lexical_score(query, candidate[2]),
                    -(publication_age_days(candidate[2]) or 0),
                ),
            )
            candidates = [freshest_relevant] + [
                candidate for candidate in candidates if candidate[0] != freshest_relevant[0]
            ]

    targets: list[str] = []
    selected_sites: set[str] = set()
    for url, site, _ in candidates:
        if site in selected_sites:
            continue
        targets.append(url)
        selected_sites.add(site)
        if len(targets) >= limit:
            return targets
    for url, _, _ in candidates:
        if url in targets:
            continue
        targets.append(url)
        if len(targets) >= limit:
            break
    return targets


def pack_evidence(entries: Sequence[dict], *, max_chars: int) -> str:
    """Pack fetched evidence into a hard character cap.

    ``entries`` are ``{"url", "ok", "text", "reason"}`` mappings. Each source
    keeps its URL and an explicit read state; when a whole source no longer
    fits it is counted in a trailing note instead of being presented as read.
    """
    if not entries or max_chars <= 0:
        return ""
    header = _HEADER
    if len(header) >= max_chars:
        return ""
    blocks: list[dict] = []
    for entry in entries:
        # Reader output may already carry renderer chunk labels; the URL and the
        # failure reason are system fields but are still defused defensively.
        url = neutralize_untrusted(str(entry.get("url") or ""))
        text = (
            neutralize_untrusted(str(entry.get("text") or ""), keep_source_labels=True)
            if entry.get("ok")
            else ""
        )
        if text:
            status = "Trạng thái: đã đọc và trích đoạn liên quan bên dưới.\n"
        else:
            reason = neutralize_untrusted(
                str(entry.get("reason") or "không có nội dung đọc được")
            )
            status = (
                f"Trạng thái: KHÔNG đọc được nguồn này ({reason}). "
                "Không được coi là đã đọc trang.\n"
            )
        blocks.append(
            {"prefix": f"\n[AUTO SOURCE] URL: {url}\n{status}", "body": text}
        )

    def render(budget: int) -> tuple[list[str], int]:
        left = budget
        parts: list[str] = []
        skipped = 0
        for block in blocks:
            prefix = block["prefix"]
            if block["body"]:
                body_budget = left - len(prefix) - 1
                if body_budget <= 0:
                    skipped += 1
                    continue
                body = block["body"][:body_budget].rstrip()
                chunk = prefix + body + "\n" if body else prefix
            else:
                chunk = prefix
            if len(chunk) > left:
                skipped += 1
                continue
            parts.append(chunk)
            left -= len(chunk)
        return parts, skipped

    parts, skipped = render(max_chars - len(header))
    if skipped:
        # Re-pack with room reserved so the unread/skipped note always fits.
        parts, skipped = render(max_chars - len(header) - _SKIP_NOTE_RESERVE)
    if skipped:
        note = f"\n(Còn {skipped} nguồn vượt ngân sách chứng cứ auto-fetch.)\n"
        rendered = len(header) + len("".join(parts)) + len(note)
        if rendered <= max_chars:
            parts.append(note)
    return header + "".join(parts)
