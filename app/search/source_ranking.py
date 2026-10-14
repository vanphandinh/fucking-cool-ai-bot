"""Lexical source ranking and independent-coverage gating for web search.

Only ``kind="web"`` uses this policy. Image search and the X/Twitter resolver
keep their existing semantics: nothing here rewrites or filters their results.

Ranking reads the query, title and snippet only. Publication dates are never
inspected, so a missing timestamp is never treated as evidence of freshness or
staleness.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..core.source_policy import canonicalize_source_url, source_site_key
from .evidence import terms

TITLE_WEIGHT = 2.0
SNIPPET_WEIGHT = 1.0
MIN_INDEPENDENT_SCORE = 0.20
MIN_INDEPENDENT_DOMAINS = 2


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


def _records(query: str, results: Sequence[dict]) -> list[dict]:
    """Score, canonical-dedupe and order results without touching the inputs."""
    records: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(results):
        if not isinstance(item, dict):
            continue
        canonical = canonicalize_source_url(item.get("url"))
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        site = source_site_key(canonical) or canonical
        records.append(
            {
                "index": index,
                "item": item,
                "url": canonical,
                "site": site,
                "score": lexical_score(query, item),
            }
        )
    records.sort(key=lambda record: (-record["score"], record["index"]))
    return records


def _as_results(records: Sequence[dict]) -> list[dict]:
    """Return result dicts in the chosen order, canonicalizing each URL."""
    return [{**record["item"], "url": record["url"]} for record in records]


def rank_web_results(query: str, results: Sequence[dict], *, limit: int = 8) -> list[dict]:
    """Rank web results by lexical relevance, then interleave domains.

    The breadth pass keeps at most one result per registrable domain so the
    most relevant sources come from independent sites before a second result
    from an already represented domain. The depth pass then fills the
    remaining slots in score order. Every deduplicated result survives unless
    ``limit`` is smaller than the number of unique URLs.
    """
    if limit <= 0:
        return []
    records = _records(query, results)
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
            return _as_results(chosen)

    for record in records:
        if record["index"] in chosen_indexes:
            continue
        chosen.append(record)
        if len(chosen) >= limit:
            break
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
