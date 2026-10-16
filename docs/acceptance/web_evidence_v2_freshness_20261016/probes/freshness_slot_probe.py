"""Diagnose why a fresh, relevant official hit can miss the top-8 slot.

Mode ``synthetic`` is deterministic and offline: it builds a pool shaped like
question 6 of the acceptance guide (many high-overlap archived pages on the
official domain, one middling recent page, one newest official release with
slightly lower term overlap) and reports what ``rank_web_results`` keeps.

Mode ``live`` replays the real search backend for the same questions and
classifies every candidate with the H1..H4 hypothesis codes from the plan.

Read-only: it never writes to production paths and never sends anything.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).parents[4]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.search.source_ranking import (  # noqa: E402
    MIN_RECENT_VERIFICATION_SCORE,
    _freshness_bonus,
    lexical_score,
    publication_age_days,
    rank_web_results,
    verification_intent,
)

OUT = ROOT / "docs/acceptance/web_evidence_v2_freshness_20261016/artifacts"

Q6 = (
    "Theo trang chính thức của NASA về chương trình Artemis, lần hạ cánh có "
    "phi hành đoàn dự kiến thuộc sứ mệnh Artemis số mấy?"
)

_OLD_TITLE = (
    "NASA Artemis chuong trinh Artemis lan ha canh co phi hanh doan su menh so may"
)


def _ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _hit(url: str, title: str, snippet: str, days: int | None) -> dict:
    item = {"url": url, "title": title, "snippet": snippet}
    if days is not None:
        item["published_at"] = _ago(days)
    return item


def synthetic_pool() -> list[dict]:
    """Question-6-shaped pool: archived official pages crowd out the release.

    Scores are not hand-waved: they come from the real ``lexical_score`` on
    these exact strings (an archived page repeating the question as both title
    and snippet scores 1.0; the official release scores ~0.32).
    """
    pool: list[dict] = []
    # Seven archived official pages with full query overlap (lexical 1.0).
    for index in range(7):
        pool.append(
            _hit(
                f"https://www.nasa.gov/artemis-archive-{index}",
                Q6,
                Q6,
                900 + index,
            )
        )
    # One middling page inside the recency window, full-overlap title only
    # (lexical ~0.67 + 0.06 recency bonus).
    pool.append(
        _hit(
            "https://www.space.com/artemis-crew-landing-overview",
            Q6,
            "",
            300,
        )
    )
    # The newest official release: genuine partial overlap (lexical ~0.32),
    # well above the 0.30 recency floor, fresh enough for the +0.15 bonus.
    pool.append(
        _hit(
            "https://www.nasa.gov/news-release/nasa-adds-mission-to-artemis-architecture",
            "chính thức NASA Artemis chương trình lần hạ cánh sứ mệnh",
            "",
            30,
        )
    )
    # A control: recent but far off topic.
    pool.append(
        _hit(
            "https://example.com/weather-today",
            "Thời tiết hôm nay",
            "Dự báo thời tiết.",
            10,
        )
    )
    return pool


def classify(query: str, pool: list[dict], limit: int, verify: bool) -> dict:
    ranked = rank_web_results(query, pool, limit=limit, verification_context=verify)
    kept = {item["url"] for item in ranked}
    rows = []
    for item in pool:
        lexical = lexical_score(query, item)
        bonus = _freshness_bonus(query, item, verification_context=verify)
        age = publication_age_days(item)
        rows.append(
            {
                "url": item["url"],
                "lexical": lexical,
                "freshness_bonus": bonus,
                "score": round(lexical + bonus, 6),
                "age_days": None if age is None else round(age, 1),
                "in_top": item["url"] in kept,
                "eligible_recent": (
                    lexical >= MIN_RECENT_VERIFICATION_SCORE
                    and age is not None
                    and age <= 365
                ),
            }
        )
    eligible_in = [r for r in rows if r["eligible_recent"] and r["in_top"]]
    eligible_out = [r for r in rows if r["eligible_recent"] and not r["in_top"]]
    if eligible_out:
        # A candidate met both the relevance floor and the recency window yet
        # did not survive the slot: the reservation mechanism failed.
        code = "H3_recency_slot_skipped"
    elif not eligible_in:
        # Nothing in the pool passed the relevance floor: the floor, not the
        # slot, is what excludes the fresh hit.
        code = "H2_no_candidate_above_floor"
    else:
        code = "H4_or_clean"
    return {
        "query": query,
        "limit": limit,
        "verification_context": verify,
        "verification_intent_matches": verification_intent(query),
        "min_recent_verification_score": MIN_RECENT_VERIFICATION_SCORE,
        "top_urls": [item["url"] for item in ranked],
        "code": code,
        "eligible_in_top": [r["url"] for r in eligible_in],
        "eligible_out_of_top": [r["url"] for r in eligible_out],
        "rows": rows,
    }


def _print(result: dict) -> None:
    print(f"\nquery={result['query'][:80]!r}")
    print(f"code={result['code']} limit={result['limit']} verify={result['verification_context']}")
    for row in sorted(result["rows"], key=lambda r: -r["score"]):
        print(
            f"  {'IN ' if row['in_top'] else 'out'} score={row['score']:.3f} "
            f"lex={row['lexical']:.3f} bonus={row['freshness_bonus']:.2f} "
            f"age={row['age_days']} recent_ok={row['eligible_recent']} {row['url'][:70]}"
        )
    print(f"  eligible_out_of_top={result['eligible_out_of_top']}")


async def _live(queries: list[tuple[int, str]], limit: int, verify: bool) -> list[dict]:
    from app.search import service as search_service
    from app.search.runtime import close_search_runtimes

    settings = Settings(_env_file=None)
    out: list[dict] = []
    try:
        for number, question in queries:
            pool = await search_service.search(
                question, settings, limit=limit, verification_context=verify
            )
            result = classify(question, pool, limit, verify)
            result["question_id"] = number
            result["pool_size"] = len(pool)
            out.append(result)
            _print(result)
    finally:
        await close_search_runtimes()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("synthetic", "live"), default="synthetic")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--verify", action="store_true", default=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.mode == "synthetic":
        result = classify(Q6, synthetic_pool(), args.limit, args.verify)
        _print(result)
        payload = {"mode": "synthetic", "results": [result]}
    else:
        import asyncio

        questions = [(6, Q6)]
        try:
            from scripts.web_evidence_live_smoke import load_questions  # noqa: PLC0415

            guide = dict(load_questions("https://x.com/jack/status/20"))
            questions = [(n, guide[n]) for n in (6, 13, 16) if n in guide]
        except Exception as exc:  # noqa: BLE001 - guide is optional for this probe
            print(f"note: falling back to the single embedded question ({type(exc).__name__})")
        results = asyncio.run(_live(questions, args.limit, args.verify))
        payload = {"mode": "live", "results": results}

    target = args.output or (OUT / f"freshness_{payload['mode']}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
