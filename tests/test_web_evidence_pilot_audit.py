"""Pilot audit metrics: what the runbook watches must be computable offline.

The audit is the pilot's monitoring view over a live-smoke artifact, so the
numbers it prints have to be right from the recorded JSON alone: latency,
provider tokens, guard removal/marker rates, and every stop condition the
artifact can prove. A citation outside the guard's allowed set must be reported
even when the answer looks clean.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.search.citation_guard import UNVERIFIED_MARKER
from scripts.web_evidence_pilot_audit import audit_rows, main, stop_reasons


def _row(
    mode: str,
    question_id: int,
    *,
    status: str = "completed",
    elapsed: float = 8.0,
    usage: dict | None = None,
    diagnostics: dict | None = None,
    answer: str = "Câu trả lời.",
    cited: list[str] | None = None,
    allowed: list[str] | None = None,
) -> dict:
    return {
        "mode": mode,
        "id": question_id,
        "status": status,
        "elapsed_seconds": elapsed,
        "provider_token_usage": usage if usage is not None else "NOT REPORTED",
        "citation_diagnostics": diagnostics,
        "answer": answer,
        "cited_urls": cited or [],
        "guard_allowed_urls": allowed,
    }


ALLOWED = "https://www.nasa.gov/mission/artemis-iii"


class AuditMetricTests(unittest.TestCase):
    def test_latency_tokens_and_rates_come_from_the_rows(self):
        rows = [
            _row("v2", 1, elapsed=4.0, usage={"prompt_tokens": 100, "completion_tokens": 10},
                 diagnostics={"detected": 2, "allowed": 1, "removed": 1, "error": ""},
                 answer=f"Xem {ALLOWED} và {UNVERIFIED_MARKER}.",
                 cited=[ALLOWED], allowed=[ALLOWED]),
            _row("v2", 2, elapsed=10.0, usage={"prompt_tokens": 200, "completion_tokens": 30},
                 diagnostics={"detected": 1, "allowed": 1, "removed": 0, "error": ""},
                 answer=f"Xem {ALLOWED}.", cited=[ALLOWED], allowed=[ALLOWED]),
            _row("v2", 3, status="timeout", elapsed=60.0),
        ]
        (metrics,) = audit_rows(rows)
        self.assertEqual((metrics.mode, metrics.rows, metrics.completed), ("v2", 3, 2))
        self.assertEqual(metrics.timeouts, 1)
        self.assertEqual(metrics.latency_median_sec, 7.0)
        self.assertEqual(metrics.latency_p95_sec, 10.0)
        self.assertEqual(metrics.prompt_tokens_median, 150)
        self.assertEqual(metrics.completion_tokens_median, 20)
        self.assertEqual(metrics.usage_rows, 2)
        self.assertEqual((metrics.guard_detected, metrics.guard_removed), (3, 1))
        self.assertAlmostEqual(metrics.removed_rate or 0.0, 1 / 3)
        self.assertEqual(metrics.marker_rows, 1)
        self.assertAlmostEqual(metrics.marker_rate or 0.0, 0.5)

    def test_missing_measurements_stay_none_not_zero(self):
        (metrics,) = audit_rows([_row("v2", 1, status="error")])
        self.assertIsNone(metrics.latency_median_sec)
        self.assertIsNone(metrics.prompt_tokens_median)
        self.assertIsNone(metrics.removed_rate)
        self.assertIsNone(metrics.marker_rate)
        self.assertEqual(metrics.usage_rows, 0)

    def test_v1_rows_never_report_guard_numbers(self):
        (metrics,) = audit_rows([_row("v1", 1, diagnostics=None, answer="Thường.")])
        self.assertEqual(metrics.guard_detected, 0)
        self.assertIsNone(metrics.removed_rate)
        self.assertEqual(metrics.errors, 0)

    def test_modes_are_reported_separately(self):
        metrics = audit_rows([_row("v1", 1), _row("v2", 1)])
        self.assertEqual([item.mode for item in metrics], ["v1", "v2"])

    def test_parenthesised_citation_is_not_an_invented_url(self):
        (metrics,) = audit_rows(
            [
                _row(
                    "v2",
                    1,
                    diagnostics={"detected": 1, "allowed": 1, "removed": 0, "error": ""},
                    answer=f"Xem {ALLOWED}).",
                    cited=[f"{ALLOWED})"],
                    allowed=[ALLOWED],
                )
            ]
        )
        self.assertEqual(metrics.invented_citations, 0)


class StopConditionTests(unittest.TestCase):
    def test_clean_v2_artifact_has_no_stop_condition(self):
        metrics = audit_rows(
            [
                _row(
                    "v2",
                    1,
                    diagnostics={"detected": 1, "allowed": 1, "removed": 1, "error": ""},
                    answer=f"Xem {UNVERIFIED_MARKER}.",
                    cited=[],
                    allowed=[ALLOWED],
                )
            ]
        )
        self.assertEqual(stop_reasons(metrics), [])

    def test_guard_error_is_a_stop_condition(self):
        metrics = audit_rows(
            [
                _row(
                    "v2",
                    1,
                    diagnostics={"detected": 1, "allowed": 0, "removed": 0, "error": "boom"},
                )
            ]
        )
        self.assertIn("guard error", stop_reasons(metrics)[0])

    def test_citation_outside_the_allowed_set_is_a_stop_condition(self):
        metrics = audit_rows(
            [
                _row(
                    "v2",
                    1,
                    diagnostics={"detected": 1, "allowed": 1, "removed": 0, "error": ""},
                    answer="Xem https://invented.example.org/x.",
                    cited=["https://invented.example.org/x"],
                    allowed=[ALLOWED],
                )
            ]
        )
        reasons = stop_reasons(metrics)
        self.assertEqual(len(reasons), 1)
        self.assertIn("outside the guard allowed set", reasons[0])

    def test_v1_rows_cannot_trigger_a_v2_stop_condition(self):
        metrics = audit_rows(
            [
                _row(
                    "v1",
                    1,
                    diagnostics={"detected": 3, "allowed": 0, "removed": 3, "error": "boom"},
                    answer="Xem https://invented.example.org/x.",
                    cited=["https://invented.example.org/x"],
                )
            ]
        )
        self.assertEqual(stop_reasons(metrics), [])


class CommandLineTests(unittest.TestCase):
    def _artifact(self, directory: str, rows: list[dict]) -> Path:
        path = Path(directory) / "artifact.json"
        path.write_text(
            json.dumps(
                {
                    "created_utc": "2026-10-16T00:00:00+00:00",
                    "quality_verdict": "NOT MEASURED",
                    "results": rows,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_clean_artifact_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._artifact(
                directory,
                [
                    _row(
                        "v2",
                        1,
                        diagnostics={"detected": 1, "allowed": 1, "removed": 1, "error": ""},
                        answer=f"Xem {UNVERIFIED_MARKER}.",
                    )
                ],
            )
            self.assertEqual(main([str(path)]), 0)

    def test_stop_condition_artifact_exits_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self._artifact(
                directory,
                [
                    _row(
                        "v2",
                        1,
                        diagnostics={"detected": 1, "allowed": 0, "removed": 0, "error": "boom"},
                    )
                ],
            )
            self.assertEqual(main([str(path)]), 1)

    def test_artifact_without_results_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text(json.dumps({"results": []}), encoding="utf-8")
            self.assertEqual(main([str(path)]), 2)


if __name__ == "__main__":
    unittest.main()
