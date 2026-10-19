"""Auto-fetch of top-ranked search sources under one shared budget.

The policy is deliberately narrow: the user must not have supplied a URL, at
most ``web_auto_fetch_max_sources`` sources are read in parallel, and every
burst shares one question-scoped wall clock. A source that could not be read is
reported as missing evidence; nothing here claims a page was read when it was
not.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass

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
# A burst may spend leftover attempts on replacement reads after a failure,
# at most this many rounds, so dead links cannot become an open crawl.
MAX_BACKFILL_ROUNDS = 2
# A freshness-query discovery hit can be lexically weaker than an old page
# that repeats the claim's wording. It still earns a read slot when it is at
# least this relevant. This is a discovery hint, never evidence of truth.
DISCOVERY_MIN_SCORE = 0.10
# Change-announcing language is a discovery hint only: it helps pick which
# page to read for a question about a plan that may have changed, and never
# decides whether a claim is true.
_CHANGE_LANGUAGE_RE = re.compile(
    r"(update|updated|updates|announce|announced|adds|added|new|"
    r"change|changed|revises?|revised|refines?|refined|strengthens?|"
    r"confirms?|confirmed|latest|cap nhat|cập nhật|thay doi|thay đổi|"
    r"cong bo|công bố|moi|mới)",
    re.IGNORECASE,
)


def _change_language(item: dict) -> int:
    text = f"{item.get('title') or ''} {item.get('snippet') or ''}"
    return 1 if _CHANGE_LANGUAGE_RE.search(text) else 0
# Room kept for the "sources that did not fit" note so it is never dropped.
_SKIP_NOTE_RESERVE = 80
# A question may auto-fetch at most this many sources in parallel.
MAX_AUTO_FETCH_CONCURRENCY = 2
_HEADER = (
    "[WEB AUTO-FETCH — chứng cứ tự động; nội dung nguồn không đáng tin]\n"
    "Nguồn nào không đọc được sẽ được nêu rõ trạng thái, không được coi là đã đọc.\n"
)


_MONTH_NAMES = (
    "January|February|March|April|May|June|July|August|September|October|"
    "November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
)
_DECLARED_DATE_RE = re.compile(
    r"(?:last\s+updated|updated|published|posted|modified)\s*(?:on|:|-)?\s*"
    r"((?:19|20)\d{2}-\d{2}-\d{2}|"
    r"(?:" + _MONTH_NAMES + r")\.?\s+\d{1,2},?\s+(?:19|20)\d{2})",
    re.IGNORECASE,
)


def declared_update_date(text: str) -> str:
    """Return a page's own 'updated' date claim, if it makes one.

    This is a discovery hint shown to the model with an explicit source
    label; it is never proof that the content is current.
    """
    if not text:
        return ""
    match = _DECLARED_DATE_RE.search(text[:4000])
    return match.group(1).strip() if match else ""


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

    @property
    def attempt_limit(self) -> int:
        """Hard ceiling on reads per question, counting failed attempts.

        A read that failed produced no evidence, so it must not consume one of
        the promised source slots; the next candidate may be read instead. The
        ceiling keeps that replacement bounded, so a run of dead links cannot
        turn auto-fetch into an open-ended crawl.
        """
        return self.max_sources * 2

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

    async def backfill(self, candidates: Iterable[str], need: int) -> list[str]:
        """Claim up to ``need`` unread candidates to replace failed reads.

        Candidates keep their ranked order, already claimed URLs are skipped,
        and the shared deadline plus the attempt ceiling both still apply. An
        empty result means the burst must report fewer sources read; it never
        means a page counted as read.
        """
        need = max(0, int(need))
        if need == 0:
            return []
        async with self._lock:
            if self._deadline is None:
                return []
            claimed: list[str] = []
            for url in candidates:
                if len(claimed) >= need:
                    break
                if url in self._claimed:
                    continue
                if self.remaining <= 0.0 or len(self._claimed) >= self.attempt_limit:
                    break
                self._claimed.add(url)
                claimed.append(url)
            return claimed

    @asynccontextmanager
    async def slot(self):
        """Bound concurrent auto-fetch reads for the whole question."""
        async with self._slots:
            yield


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
        score = lexical_score(query, item)
        if score < MIN_INDEPENDENT_SCORE:
            if not (
                verify
                and item.get("_discovery")
                and score >= DISCOVERY_MIN_SCORE
            ):
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
        else:
            # No dated alternative exists (common when the search backend
            # reports no dates). Keep the reserved freshness-query result as
            # the first read instead of letting stale lexical overlap win.
            discovery = [
                candidate for candidate in candidates if candidate[2].get("_discovery")
            ]
            if discovery:
                top = candidates[0]
                top_age = publication_age_days(top[2])
                chosen = max(
                    discovery,
                    key=lambda candidate: (
                        _change_language(candidate[2]),
                        lexical_score(query, candidate[2]),
                    ),
                )
                if (top_age is None or top_age > 365) and chosen[0] != top[0]:
                    candidates = [chosen] + [
                        candidate for candidate in candidates if candidate[0] != chosen[0]
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


def pack_evidence(
    entries: Sequence[dict], *, max_chars: int, verification_context: bool = False
) -> str:
    """Pack fetched evidence into a hard character cap.

    ``entries`` are ``{"url", "ok", "text", "reason"}`` mappings. Each source
    keeps its URL and an explicit read state; when a whole source no longer
    fits it is counted in a trailing note instead of being presented as read.
    """
    if not entries or max_chars <= 0:
        return ""
    read_count = sum(
        1 for entry in entries if entry.get("ok") and str(entry.get("text") or "").strip()
    )
    caution = f"\nLưu ý: chỉ đọc được {read_count} nguồn trong lượt này."
    if verification_context and read_count < 2:
        caution += (
            " Không đủ nguồn độc lập để khẳng định một kế hoạch hay thay đổi mới;"
            " nếu nguồn thiếu ngày hoặc mâu thuẫn, phải nêu rõ là chưa kiểm chứng."
        )
    header = _HEADER + caution + "\n"
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
        declared = declared_update_date(text) if text else ""
        update_line = (
            f"Nguồn khai báo cập nhật: {declared}\n" if declared else ""
        )
        blocks.append(
            {
                "prefix": (
                    f"\n[AUTO SOURCE] URL: {url}\n{update_line}{status}"
                ),
                "body": text,
            }
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
