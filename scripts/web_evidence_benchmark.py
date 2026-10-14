"""Reproducible offline replay of the web evidence pipeline.

Search and HTTP are replaced with original synthetic snapshots, while actual
search routing, URL orchestration, generic reader and synthesis packing code
execute. Three arms are compared on the same fixture and fetch policy:

``v1``    current production V1 behaviour (8000-char reader, 12000-char synthesis);
``chars`` V1 with only the character limits raised (simulated configuration);
``v2``    adaptive evidence selection + source ranking under the V2 flag.

The extractive answer/citation judge is a *proxy*, not an evaluation of an LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import statistics
import time
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.ai.contracts import ChatMessage, TextPart
from app.ai.synthesis import MAX_SYNTHESIS_EVIDENCE_CHARS, build_fresh_synthesis_messages
from app.config import Settings
from app.core.orchestrator import _format_search_results
from app.search import reader, service, url_service

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests/fixtures/web_evidence_v2/cases.json"
DEFAULT_REPORT = ROOT / "docs/benchmarks/web_evidence_v1_baseline.json"
CONFIGS = ("v1", "chars", "v2")
# Simulated "only raise the character limits" arm; not a shipped configuration.
CHARS_ONLY_READER_LIMIT = 32000
CHARS_ONLY_SYNTHESIS_LIMIT = 32000
# Kept out of the web-evidence framing so non-web outputs can be checked for
# survival independently of page content.
NON_WEB_TOOL_OUTPUT = "[TOOL_RESULT] Máy tính nội bộ: 7 × 3 = 21"
NON_WEB_MARKER = "Máy tính nội bộ"
LABEL_RE = re.compile(r"\[(?:SOURCE \d+ / CHUNK \d+|AUTO SOURCE)\]")
FACT_RE = re.compile(r"Dữ kiện:\s*(\d+)", re.IGNORECASE)
FIXTURE_CATEGORIES = {
    "cases.json": {
        "fact": 10,
        "buried": 8,
        "multi_source": 8,
        "fresh": 7,
        "unknown_conflicting": 7,
    },
    "cases_100.json": {
        "fact": 30,
        "buried": 20,
        "multi_source": 20,
        "fresh": 15,
        "unknown_conflicting": 15,
    },
}


def load_cases(path: Path = FIXTURE) -> list[dict]:
    expected = FIXTURE_CATEGORIES.get(path.name)
    assert expected is not None, f"unknown fixture {path.name}"
    cases = json.loads(path.read_text(encoding="utf-8"))
    assert Counter(case["category"] for case in cases) == expected
    assert len({case["id"] for case in cases}) == sum(expected.values())
    for case in cases:
        assert case["query"] and case["snapshot_kind"] == "original synthetic offline fixture"
        assert len(case["search_results"]) == 8
        assert case["expected_uncertainty"] in ("supported", "abstain")
        urls = {page["url"] for page in case["sources"]}
        assert urls == set(case["allowed_sources"])
        assert all(url.startswith("https://") and ".example." in url for url in urls)
        assert all(
            hit["url"].startswith("https://") and ".example." in hit["url"]
            for hit in case["search_results"]
        )
        if case["category"] == "fresh":
            assert case["as_of"]
        assert (case["gold_answer"] is None) == (case["expected_uncertainty"] == "abstain")
    return cases


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    return round(ordered[max(0, math.ceil(len(ordered) * percentile / 100) - 1)], 3)


async def replay(case: dict, *, config: str = "v1", fixture: Path = FIXTURE) -> dict:
    """Replay fixed policy: search eight, read first two, pack evidence.

    The real app lets the LLM choose which URLs to fetch. This deterministic
    fixed policy isolates retrieval, extraction and packing quality. Conditional
    auto-fetch is never enabled here, so it stays unmeasured offline.
    """
    assert config in CONFIGS
    settings = Settings(
        _env_file=None,
        search_backend="auto",
        searxng_url="https://searx.example.org",
        web_evidence_v2_enabled=(config == "v2"),
    )
    pages = {item["url"]: item for item in case["sources"]}
    durations: dict[str, float] = {}
    searx = AsyncMock(return_value=case["search_results"])
    ddgs = AsyncMock(return_value=[])

    search_payload = ""
    outputs: list[str] = []
    citations: list[str] = []
    statements: list[str] = []
    fetch_count = 0

    async def fake_fetch(_client, url: str, max_bytes: int = reader.MAX_BYTES):
        if url.startswith("https://r.jina.ai/") or url not in pages:
            raise reader._FetchError("offline fixture: unavailable")
        data = pages[url]["html"].encode("utf-8")
        if len(data) > max_bytes:
            raise reader._FetchError("too large")
        return reader._FetchResult(data, "utf-8")

    # The character-limit arm must cover reading too, not only search.
    with ExitStack() as stack:
        stack.enter_context(patch("app.search.searxng_backend.search_searxng", new=searx))
        stack.enter_context(patch("app.search.ddgs_backend.search_ddgs", new=ddgs))
        stack.enter_context(patch("app.search.reader._fetch_limited", new=fake_fetch))
        if config == "chars":
            stack.enter_context(patch("app.search.reader.MAX_CHARS", CHARS_ONLY_READER_LIMIT))

        start = time.perf_counter()
        results = await service.search(case["query"], settings)
        durations["search_ms"] = (time.perf_counter() - start) * 1000

        search_payload = _format_search_results(case["query"], results)
        outputs = [search_payload]

        start = time.perf_counter()
        for hit in results[:2]:
            fetch_count += 1
            read = await url_service.read_url(
                hit["url"],
                settings,
                **({"query": case["query"]} if config == "v2" else {}),
            )
            if not read.ok:
                continue
            outputs.append(f"Nội dung URL {read.source_url}:\n{read.text}")
            claims = FACT_RE.findall(read.text)
            for claim in claims:
                statements.append(claim)
                citations.append(read.source_url)
        durations["fetch_ms"] = (time.perf_counter() - start) * 1000

    outputs.append(NON_WEB_TOOL_OUTPUT)
    synthesis_cap = (
        CHARS_ONLY_SYNTHESIS_LIMIT if config == "chars" else MAX_SYNTHESIS_EVIDENCE_CHARS
    )
    start = time.perf_counter()
    packed = build_fresh_synthesis_messages(
        [ChatMessage("user", (TextPart(case["query"]),))],
        outputs,
        max_evidence_chars=synthesis_cap,
    )
    synthesis_text = packed[-1].parts[0].text if packed else ""
    durations["synthesis_ms"] = (time.perf_counter() - start) * 1000

    # Source-backed extractive proxy: read only actual returned page text, never
    # case gold labels. For conflicting values abstain. A real model may behave
    # differently; PR4 must use independently scored model predictions.
    unique = list(dict.fromkeys(statements))
    if len(unique) > 1 and case["category"] == "unknown_conflicting":
        answer = None
        citations = []
    else:
        answer = "; ".join(statements) if statements else None
    expected_answer = case["gold_answer"]
    accurate = answer == expected_answer

    # Evidence recall counts phrases in successfully fetched page output only,
    # before model synthesis. Unknown questions have no gold evidence recall.
    fetched = "\n".join(outputs[1:-1])
    evidence_expected = case["expected_evidence"]
    evidence_hits = sum(phrase in fetched for phrase in evidence_expected)
    # Packing recall counts the same phrases inside the synthesis context that
    # the model actually receives, which is what the character caps bound.
    packed_hits = sum(phrase in synthesis_text for phrase in evidence_expected)
    available = {item["url"] for item in results[:8]}
    source_hits = len(available & set(case["allowed_sources"]))
    supported_citations = sum(
        url in pages and f"Dữ kiện: {statement}" in pages[url]["html"]
        for statement, url in zip(statements, citations)
    )

    return {
        "id": case["id"],
        "category": case["category"],
        "source_hits": source_hits,
        "source_total": len(case["allowed_sources"]),
        "evidence_hits": evidence_hits,
        "evidence_total": len(evidence_expected),
        "packed_evidence_hits": packed_hits,
        "packed_label_count": len(LABEL_RE.findall(synthesis_text)),
        "non_web_output_preserved": NON_WEB_MARKER in synthesis_text,
        "answer_correct_proxy": accurate,
        "cited_claims_proxy": len(citations),
        "supported_citations_proxy": supported_citations,
        "unsupported_claims_proxy": len(citations) - supported_citations,
        "total_claims_proxy": len(citations),
        "fetch_count": fetch_count,
        # Explicit estimate, not provider API token usage.
        "estimated_request_tokens": (len(search_payload) + len(synthesis_text) + 3) // 4,
        "durations_ms": {key: round(val, 3) for key, val in durations.items()},
    }


async def evaluate(
    cases: list[dict] | None = None,
    *,
    config: str = "v1",
    fixture: Path = FIXTURE,
) -> dict:
    cases = cases if cases is not None else load_cases(fixture)
    rows = [await replay(case, config=config, fixture=fixture) for case in cases]

    def totals(key: str) -> int:
        return sum(row[key] for row in rows)

    durations = {
        stage: {
            "p50": _percentile([row["durations_ms"][stage] for row in rows], 50),
            "p95": _percentile([row["durations_ms"][stage] for row in rows], 95),
        }
        for stage in ("search_ms", "fetch_ms", "synthesis_ms")
    }
    durations["total_ms"] = {
        "p50": _percentile([sum(row["durations_ms"].values()) for row in rows], 50),
        "p95": _percentile([sum(row["durations_ms"].values()) for row in rows], 95),
    }
    total_claims = totals("total_claims_proxy")
    evidence_total = totals("evidence_total")
    return {
        "config": config,
        "pipeline": {
            "v1": "V1",
            "chars": "V1-expanded-character-limits",
            "v2": "V2-adaptive-evidence",
        }[config],
        "case_count": len(rows),
        "category_counts": dict(Counter(row["category"] for row in rows)),
        "fixture": fixture.name,
        "fixture_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
        "evaluation_method": (
            "offline intercepted search/HTTP; fixed top-two fetch; extractive proxy"
        ),
        "reader_char_limit": CHARS_ONLY_READER_LIMIT if config == "chars" else reader.MAX_CHARS,
        "synthesis_char_limit": (
            CHARS_ONLY_SYNTHESIS_LIMIT if config == "chars" else MAX_SYNTHESIS_EVIDENCE_CHARS
        ),
        "network_requests": 0,
        "metrics": {
            "source_recall_at_8": round(totals("source_hits") / totals("source_total"), 4),
            "evidence_recall": round(totals("evidence_hits") / evidence_total, 4)
            if evidence_total
            else None,
            "synthesis_evidence_recall": round(totals("packed_evidence_hits") / evidence_total, 4)
            if evidence_total
            else None,
            "synthesis_labeled_excerpt_count": totals("packed_label_count"),
            "synthesis_non_web_preservation_rate": round(
                sum(row["non_web_output_preserved"] for row in rows) / len(rows), 4
            ),
            "answer_accuracy_extractive_proxy": round(
                totals("answer_correct_proxy") / len(rows), 4
            ),
            "citation_precision_extractive_proxy": round(
                totals("supported_citations_proxy") / total_claims, 4
            )
            if total_claims
            else None,
            "unsupported_claim_rate_extractive_proxy": round(
                totals("unsupported_claims_proxy") / total_claims, 4
            )
            if total_claims
            else None,
            "fetch_count_mean": round(totals("fetch_count") / len(rows), 3),
            "request_tokens_median_estimate": statistics.median(
                row["estimated_request_tokens"] for row in rows
            ),
            "request_tokens_mean_estimate": round(
                statistics.fmean(row["estimated_request_tokens"] for row in rows), 1
            ),
            "request_tokens_p90_estimate": _percentile(
                [float(row["estimated_request_tokens"]) for row in rows], 90
            ),
            "latency_ms": durations,
        },
        "not_measured": [
            "real_model_answer_accuracy",
            "human_checked_citation_precision",
            "real_model_unsupported_claim_rate",
            "provider_request_tokens",
            "production_network_latency",
            "conditional_auto_fetch",
            "live_rollout_safety",
        ],
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--config",
        choices=CONFIGS,
        default="v1",
        help="v1 baseline, v1 with raised character limits, or adaptive V2",
    )
    parser.add_argument("--v2", action="store_true", help="Alias for --config v2")
    parser.add_argument("--fixture", type=Path, default=FIXTURE)
    args = parser.parse_args()
    config = "v2" if args.v2 else args.config
    fixture = args.fixture
    report = asyncio.run(evaluate(config=config, fixture=fixture))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {report['case_count']} results to {args.output}")
    else:
        print(
            json.dumps(
                {key: value for key, value in report.items() if key != "cases"},
                indent=2,
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
