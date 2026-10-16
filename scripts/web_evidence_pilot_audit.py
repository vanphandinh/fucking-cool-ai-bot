"""Pilot monitoring audit for a live-smoke artifact.

Reads one ``scripts/web_evidence_live_smoke.py`` artifact and prints the numbers
the pilot runbook watches — latency, real provider token usage, guard removal
rate, how often the unverified marker reached the user, and citations that live
outside the guard's allowed set — plus an explicit verdict on the stop
conditions that force V2 OFF. Read-only and offline: no network, no provider.

Run from repo root:
    python scripts/web_evidence_pilot_audit.py <artifact.json>

Exit code 0 = no stop condition observed in the artifact; 1 = a stop condition.
An exit code of 0 is not a quality verdict: the artifact's own
``quality_verdict`` and the manual rubric still decide the pilot.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):
    repo_root = str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

from app.search.citation_guard import UNVERIFIED_MARKER  # noqa: E402


@dataclass(frozen=True)
class ModeMetrics:
    mode: str
    rows: int
    completed: int
    timeouts: int
    errors: int
    latency_median_sec: float | None
    latency_p95_sec: float | None
    prompt_tokens_median: int | None
    completion_tokens_median: int | None
    usage_rows: int
    guard_detected: int
    guard_removed: int
    guard_errors: int
    invented_citations: int
    marker_rows: int

    @property
    def removed_rate(self) -> float | None:
        """Share of detected citations the guard had to rewrite."""
        if not self.guard_detected:
            return None
        return self.guard_removed / self.guard_detected

    @property
    def marker_rate(self) -> float | None:
        """Share of completed answers that carried the unverified marker."""
        if not self.completed:
            return None
        return self.marker_rows / self.completed


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)]


def _median(values: list[int]) -> float | None:
    return statistics.median(values) if values else None


def _unexplained_citations(row: dict) -> list[str]:
    """Cited URLs the guard never allowed, tolerating its sentence punctuation.

    The harness regex can stop a URL at ``)``; a parenthesised citation is
    valid, so a cited URL that is a prefix of an allowed URL counts as allowed.
    """
    allowed = set(row.get("guard_allowed_urls") or [])
    cited = [url for url in (row.get("cited_urls") or []) if url]
    unexplained: list[str] = []
    for url in cited:
        if url in allowed:
            continue
        if any(candidate.startswith(url) for candidate in allowed):
            continue
        trimmed = url[:-1] if not url[-1].isalnum() else url
        if trimmed in allowed:
            continue
        unexplained.append(url)
    return unexplained


def audit_rows(rows: list[dict]) -> list[ModeMetrics]:
    by_mode: dict[str, list[dict]] = {}
    for row in rows:
        by_mode.setdefault(str(row.get("mode") or "?"), []).append(row)

    metrics: list[ModeMetrics] = []
    for mode, mode_rows in sorted(by_mode.items()):
        latencies = [
            float(row.get("elapsed_seconds") or 0.0)
            for row in mode_rows
            if row.get("status") == "completed"
        ]
        statuses = Counter(str(row.get("status") or "?") for row in mode_rows)
        usage_rows = [
            row["provider_token_usage"]
            for row in mode_rows
            if isinstance(row.get("provider_token_usage"), dict)
        ]
        detected = removed = errors = invented = markers = 0
        for row in mode_rows:
            diagnostics = row.get("citation_diagnostics")
            if isinstance(diagnostics, dict):
                detected += int(diagnostics.get("detected") or 0)
                removed += int(diagnostics.get("removed") or 0)
                errors += 1 if diagnostics.get("error") else 0
                invented += len(_unexplained_citations(row))
            if UNVERIFIED_MARKER in str(row.get("answer") or ""):
                markers += 1
        metrics.append(
            ModeMetrics(
                mode=mode,
                rows=len(mode_rows),
                completed=statuses.get("completed", 0),
                timeouts=statuses.get("timeout", 0),
                errors=sum(
                    count
                    for status, count in statuses.items()
                    if status not in ("completed", "timeout")
                ),
                latency_median_sec=statistics.median(latencies) if latencies else None,
                latency_p95_sec=_p95(latencies) if latencies else None,
                prompt_tokens_median=_median(
                    [
                        usage["prompt_tokens"]
                        for usage in usage_rows
                        if isinstance(usage.get("prompt_tokens"), int)
                    ]
                ),
                completion_tokens_median=_median(
                    [
                        usage["completion_tokens"]
                        for usage in usage_rows
                        if isinstance(usage.get("completion_tokens"), int)
                    ]
                ),
                usage_rows=len(usage_rows),
                guard_detected=detected,
                guard_removed=removed,
                guard_errors=errors,
                invented_citations=invented,
                marker_rows=markers,
            )
        )
    return metrics


def stop_reasons(metrics: list[ModeMetrics]) -> list[str]:
    """Artifact-checkable stop conditions from the pilot runbook, in order."""
    reasons: list[str] = []
    for item in metrics:
        if item.mode != "v2":
            continue
        if item.guard_errors:
            reasons.append(
                f"v2: {item.guard_errors} guard error(s) — an answered question was lost"
            )
        if item.invented_citations:
            reasons.append(
                f"v2: {item.invented_citations} citation(s) outside the guard allowed set"
            )
    return reasons


def _format(value: object, *, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _print_metrics(metrics: list[ModeMetrics]) -> None:
    header = (
        "mode rows done fail timeout p50_s p95_s prompt_tok completion_tok "
        "usage_rows detected removed removed_rate errors invented marker_rate"
    )
    print(header)
    for item in metrics:
        print(
            f"{item.mode:>4} {item.rows:>4} {item.completed:>4} {item.errors:>4} "
            f"{item.timeouts:>7} {_format(item.latency_median_sec):>5} "
            f"{_format(item.latency_p95_sec):>5} "
            f"{_format(item.prompt_tokens_median)} {_format(item.completion_tokens_median)} "
            f"{item.usage_rows:>10} {item.guard_detected:>8} {item.guard_removed:>7} "
            f"{_format(item.removed_rate, digits=3):>12} {item.guard_errors:>6} "
            f"{item.invented_citations:>8} {_format(item.marker_rate, digits=3):>11}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path, help="live smoke artifact JSON")
    args = parser.parse_args(argv)

    report = json.loads(args.artifact.read_text(encoding="utf-8"))
    rows = report.get("results") or []
    if not rows:
        print(f"{args.artifact}: no results")
        return 2

    metrics = audit_rows(rows)
    print(
        f"artifact={args.artifact} created_utc={report.get('created_utc')} "
        f"quality_verdict={report.get('quality_verdict')}"
    )
    _print_metrics(metrics)
    print(
        "note: V1 rows never run the guard, so their guard columns stay zero; "
        "the marker rate is only meaningful for v2."
    )

    reasons = stop_reasons(metrics)
    if reasons:
        print("STOP CONDITIONS:")
        for reason in reasons:
            print(f"- {reason}")
        return 1
    print("no artifact-checkable stop condition observed (quality rubric still applies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
