"""Source ranking and independent-coverage gating for web search.

Only ``kind="web"`` uses this policy. Image search and the X/Twitter resolver
keep their existing semantics: nothing here rewrites or filters their results.

For questions asking for recent information or verifying a disputed claim,
publication timestamps can add a small recency signal. A timestamp is a
discovery hint, never evidence that a claim is correct. No domain suffix is
treated as authoritative for every topic.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
import re

from ..core.source_policy import canonicalize_source_url, source_site_key
from .evidence import normalize, terms

TITLE_WEIGHT = 2.0
SNIPPET_WEIGHT = 1.0
MIN_INDEPENDENT_SCORE = 0.20
MIN_INDEPENDENT_DOMAINS = 2
# Keep one moderately relevant dated alternative discoverable during fact
# checks, without promoting a fresh but unrelated page into the top results.
MIN_RECENT_VERIFICATION_SCORE = 0.30

_VERIFICATION_INTENT = re.compile(
    r"\b(xac minh|kiem chung|kiem tra tinh xac thuc|thuc hu|"
    r"dung hay sai|dung sai|co dung khong|co that khong|"
    r"fact.check|verify|verification|is it true|true or false|"
    r"chinh thuc|latest|current|as of|moi nhat|cap nhat|hien tai|hien nay)\b"
)


def verification_intent(question: str) -> bool:
    """Recognize requests where snippets alone should not settle a claim."""
    return bool(_VERIFICATION_INTENT.search(normalize(question)))


def publication_age_days(item: dict) -> float | None:
    """Return non-future age of a parseable publication timestamp, if known."""
    published_at = item.get("published_at")
    if not isinstance(published_at, str) or len(published_at) > 40:
        return None
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - published).total_seconds() / 86400
    return age if age >= 0 else None


def _freshness_bonus(query: str, item: dict, *, verification_context: bool = False) -> float:
    """A bounded recency hint for current/verification discovery."""
    normalized = normalize(query)
    if not verification_context and not re.search(
        r"\b(latest|current|today|recent|updated|update|"
        r"moi nhat|gan day|hien tai|hom nay|cap nhat)\b", normalized,
    ):
        return 0.0
    age = publication_age_days(item)
    if age is None:
        return 0.0
    if age <= 120:
        return 0.15
    if age <= 365:
        return 0.06
    return 0.0


def _coverage(wanted: set[str], text: str) -> float:
    if not wanted:
        return 0.0
    return len(wanted & terms(text)) / len(wanted)


def title_score(query: str, item: dict) -> float:
    """Normalized query coverage of the result title."""
    return _coverage(terms(query), str(item.get("title") or ""))


def snippet_score(query: str, item: dict) -> float:
    """Normalized query coverage of the result snippet."""
    return _coverage(terms(query), str(item.get("snippet") or ""))


def lexical_score(query: str, item: dict) -> float:
    """Weighted normalized query coverage: title counts twice the snippet."""
    wanted = terms(query)
    if not wanted:
        return 0.0
    weighted = TITLE_WEIGHT * _coverage(wanted, str(item.get("title") or ""))
    weighted += SNIPPET_WEIGHT * _coverage(wanted, str(item.get("snippet") or ""))
    return round(min(1.0, weighted / (TITLE_WEIGHT + SNIPPET_WEIGHT)), 6)


def _records(
    query: str, results: Sequence[dict], *, verification_context: bool = False
) -> list[dict]:
    """Score, canonical-dedupe and order results without touching the inputs."""
    best_by_url: dict[str, dict] = {}
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        canonical = canonicalize_source_url(item.get("url"))
        if not canonical:
            continue
        site = source_site_key(canonical) or canonical
        score = lexical_score(query, item) + _freshness_bonus(
            query, item, verification_context=verification_context
        )
        previous = best_by_url.get(canonical)
        # Search engines often supply different excerpts for the same page.
        # Keep the stronger one; equal-score ties retain the earliest result.
        if previous is None or score > previous["score"]:
            best_by_url[canonical] = {
                "index": index,
                "item": item,
                "url": canonical,
                "site": site,
                "score": score,
            }
    records = list(best_by_url.values())
    records.sort(key=lambda record: (-record["score"], record["index"]))
    return records


def _as_results(records: Sequence[dict]) -> list[dict]:
    """Return result dicts in the chosen order, canonicalizing each URL."""
    return [{**record["item"], "url": record["url"]} for record in records]


def rank_web_results(
    query: str,
    results: Sequence[dict],
    *,
    limit: int = 8,
    verification_context: bool = False,
) -> list[dict]:
    """Rank web results by lexical relevance, then interleave domains.

    The breadth pass keeps at most one result per registrable domain so the
    most relevant sources come from independent sites before a second result
    from an already represented domain. The depth pass then fills the
    remaining slots in score order. Every deduplicated result survives unless
    ``limit`` is smaller than the number of unique URLs.
    """
    if limit <= 0:
        return []
    verify = verification_context or verification_intent(query)
    records = _records(
        query, results,
        verification_context=verify,
    )
    if not records:
        return []

    chosen: list[dict] = []
    chosen_indexes: set[int] = set()
    seen_sites: set[str] = set()
    for record in records:
        if record["site"] in seen_sites:
            continue
        seen_sites.add(record["site"])
        chosen.append(record)
        chosen_indexes.add(record["index"])
        if len(chosen) >= limit:
            break

    if len(chosen) < limit:
        for record in records:
            if record["index"] in chosen_indexes:
                continue
            chosen.append(record)
            if len(chosen) >= limit:
                break

    # In a full pool of high-overlap historical results, a changed policy may
    # use different terminology and be pushed past the display cap. During a
    # verification query, reserve one slot for an independently relevant,
    # recently dated alternative. Publication metadata is a discovery hint:
    # it does not make the newer statement true or authoritative.
    if verify and len(chosen) == limit:
        recent = [
            record for record in records
            if lexical_score(query, record["item"]) >= MIN_RECENT_VERIFICATION_SCORE
            and (age := publication_age_days(record["item"])) is not None
            and age <= 365
        ]
        if recent and not any(record in chosen for record in recent):
            selected_sites = {record["site"] for record in chosen}
            # Prefer a novel domain; otherwise replace that domain's older hit.
            fresh = next((r for r in recent if r["site"] not in selected_sites), recent[0])
            replace = next(
                (i for i, r in enumerate(chosen) if r["site"] == fresh["site"]),
                len(chosen) - 1,
            )
            chosen[replace] = fresh
    return _as_results(chosen)


def independent_domains(
    query: str,
    results: Sequence[dict],
    *,
    threshold: float = MIN_INDEPENDENT_SCORE,
) -> set[str]:
    """Registrable domains of results scoring at least ``threshold``."""
    domains: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        if lexical_score(query, item) < threshold:
            continue
        canonical = canonicalize_source_url(item.get("url"))
        if not canonical:
            continue
        domains.add(source_site_key(canonical) or canonical)
    return domains


def has_independent_coverage(
    query: str,
    results: Sequence[dict],
    *,
    threshold: float = MIN_INDEPENDENT_SCORE,
    min_domains: int = MIN_INDEPENDENT_DOMAINS,
) -> bool:
    """True when enough independent relevant domains are already present.

    ``SEARCH_BACKEND=auto`` uses this to decide whether SearXNG results alone
    are good enough, or whether a DDGS top-up is still required.
    """
    return len(independent_domains(query, results, threshold=threshold)) >= min_domains
