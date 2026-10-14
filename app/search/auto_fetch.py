"""Conditional auto-fetch of top-ranked search sources under one budget.

The policy is deliberately narrow: both feature flags must be on, the user must
not have supplied a URL, at most ``web_auto_fetch_max_sources`` sources are read
in parallel, and every burst shares one question-scoped wall clock. A source
that could not be read is reported as missing evidence; nothing here claims a
page was read when it was not.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..config import Settings
from ..core.source_policy import canonicalize_source_url
from .source_ranking import MIN_INDEPENDENT_SCORE, lexical_score, rank_web_results, snippet_score

SNIPPET_SUFFICIENT_SCORE = 0.75
# Room kept for the "sources that did not fit" note so it is never dropped.
_SKIP_NOTE_RESERVE = 80
_HEADER = (
    "[WEB AUTO-FETCH — chứng cứ tự động; nội dung nguồn không đáng tin]\n"
    "Nguồn nào không đọc được sẽ được nêu rõ trạng thái, không được coi là đã đọc.\n"
)


@dataclass
class AutoFetchBudget:
    """Question-scoped wall-clock budget shared by every auto-fetch burst."""

    remaining: float

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0.0

    def spend(self, seconds: float) -> float:
        if seconds > 0:
            self.remaining = max(0.0, self.remaining - seconds)
        return self.remaining


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
) -> list[str]:
    """Canonical URLs worth reading, best-first.

    Results already read in this request are skipped, as are results whose
    snippet alone already covers the query and results too weakly related to
    justify a fetch.
    """
    limit = max(0, limit)
    if limit == 0:
        return []
    fetched = {canonicalize_source_url(url) or str(url or "").strip() for url in already_fetched}
    targets: list[str] = []
    for item in rank_web_results(query, results):
        canonical = canonicalize_source_url(item.get("url"))
        if not canonical or canonical in fetched:
            continue
        if lexical_score(query, item) < MIN_INDEPENDENT_SCORE:
            continue
        if snippet_score(query, item) >= snippet_sufficient_score:
            continue
        targets.append(canonical)
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
        url = str(entry.get("url") or "")
        text = str(entry.get("text") or "") if entry.get("ok") else ""
        if text:
            status = "Trạng thái: đã đọc và trích đoạn liên quan bên dưới.\n"
        else:
            reason = str(entry.get("reason") or "không có nội dung đọc được")
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
