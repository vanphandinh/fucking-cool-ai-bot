"""Offline benchmark contract and deterministic replay coverage.

The 40-case fixture stays frozen for reproducible reports; the 100-case fixture
drives the V2 coverage checks. Both files are generated from the same entry
lists.
"""

from __future__ import annotations

import hashlib
import unittest
from collections import Counter
from pathlib import Path

from scripts.generate_web_evidence_fixtures import (
    DESTINATION,
    DESTINATION_100,
    main as generate_fixtures,
)
from scripts.web_evidence_benchmark import (
    FIXTURE_CATEGORIES,
    evaluate,
    evaluate_auto_fetch,
    load_cases,
    replay,
    replay_auto_fetch,
)

CATEGORIES = FIXTURE_CATEGORIES["cases.json"]
CATEGORIES_100 = FIXTURE_CATEGORIES["cases_100.json"]
FIXTURE_100 = Path(DESTINATION_100)


class WebEvidenceFixturesTests(unittest.TestCase):
    def test_generated_snapshots_match_checked_in_fixtures(self) -> None:
        before = DESTINATION.read_bytes()
        before_100 = FIXTURE_100.read_bytes()
        generate_fixtures()
        self.assertEqual(DESTINATION.read_bytes(), before)
        self.assertEqual(FIXTURE_100.read_bytes(), before_100)

    def test_all_40_cases_have_gold_and_bounded_discovery_data(self) -> None:
        cases = load_cases()
        self.assertEqual(Counter(case["category"] for case in cases), CATEGORIES)
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(len(case["search_results"]), 8)
                self.assertTrue(case["allowed_sources"])
                self.assertTrue(all("example." in url for url in case["allowed_sources"]))
                self.assertTrue(case["query"])
                if case["expected_uncertainty"] == "supported":
                    self.assertTrue(case["gold_answer"])
                    self.assertTrue(case["expected_evidence"])
                else:
                    self.assertIsNone(case["gold_answer"])

    def test_fixture_contains_deep_and_conflicting_evidence(self) -> None:
        cases = load_cases()
        buried = [case for case in cases if case["category"] == "buried"]
        for case in buried:
            html = case["sources"][0]["html"]
            self.assertGreater(html.index(case["expected_evidence"][0]), 8000)
        conflicts = [case for case in cases if case["id"].startswith("unknown-0")]
        self.assertEqual(sum(len(case["sources"]) == 2 for case in conflicts), 3)

    def test_100_case_fixture_has_required_category_counts(self) -> None:
        cases = load_cases(FIXTURE_100)
        self.assertEqual(len(cases), 100)
        self.assertEqual(Counter(case["category"] for case in cases), CATEGORIES_100)
        legacy = {case["id"] for case in load_cases()}
        self.assertTrue(legacy < {case["id"] for case in cases})

    def test_100_case_fixture_keeps_deep_fresh_and_conflict_invariants(self) -> None:
        cases = load_cases(FIXTURE_100)
        buried = [case for case in cases if case["category"] == "buried"]
        self.assertEqual(len(buried), 20)
        for case in buried:
            html = case["sources"][0]["html"]
            self.assertGreater(html.index(case["expected_evidence"][0]), 8000)
        fresh = [case for case in cases if case["category"] == "fresh"]
        self.assertEqual(len(fresh), 15)
        self.assertTrue(all(case["as_of"] for case in fresh))
        conflicts = [case for case in cases if case["expected_uncertainty"] == "abstain"]
        self.assertEqual(len(conflicts), 15)
        self.assertEqual(sum(len(case["sources"]) == 2 for case in conflicts), 7)
        self.assertTrue(all(case["gold_answer"] is None for case in conflicts))


class WebEvidenceReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_pipeline_reaches_buried_conclusion(self) -> None:
        case = next(case for case in load_cases() if case["id"] == "buried-01")
        result = await replay(case)
        self.assertEqual(result["evidence_hits"], 1)
        self.assertTrue(result["answer_correct_proxy"])

    async def test_known_answer_is_extracted_from_a_fetched_source(self) -> None:
        case = next(case for case in load_cases() if case["id"] == "fact-01")
        result = await replay(case)
        self.assertEqual(result["evidence_hits"], 1)
        self.assertTrue(result["answer_correct_proxy"])
        self.assertEqual(result["unsupported_claims_proxy"], 0)

    async def test_report_metrics_are_bounded_and_replayable(self) -> None:
        cases = load_cases()
        first = await evaluate(cases)
        second = await evaluate(cases)
        self.assertEqual(first["fixture_sha256"], second["fixture_sha256"])
        self.assertEqual(first["case_count"], 40)
        self.assertEqual(first["network_requests"], 0)
        self.assertEqual(first["config"], "v2")
        self.assertEqual(first["pipeline"], "V2-adaptive-evidence")
        self.assertEqual(first["category_counts"], dict(CATEGORIES))
        self.assertEqual(
            [
                {key: value for key, value in row.items() if key != "durations_ms"}
                for row in first["cases"]
            ],
            [
                {key: value for key, value in row.items() if key != "durations_ms"}
                for row in second["cases"]
            ],
        )
        for name in (
            "source_recall_at_8",
            "evidence_recall",
            "synthesis_evidence_recall",
            "answer_accuracy_extractive_proxy",
            "citation_precision_extractive_proxy",
            "unsupported_claim_rate_extractive_proxy",
        ):
            self.assertGreaterEqual(first["metrics"][name], 0)
            self.assertLessEqual(first["metrics"][name], 1)
        self.assertEqual(first["metrics"]["synthesis_non_web_preservation_rate"], 1.0)
        self.assertIn("real_model_answer_accuracy", first["not_measured"])


class AutoFetchReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_fetch_replay_bounds_sources_concurrency_and_output(self) -> None:
        cases = load_cases()
        report = await evaluate_auto_fetch(cases)
        self.assertEqual(report["case_count"], 40)
        self.assertEqual(
            report["fixture_sha256"],
            hashlib.sha256(Path(DESTINATION).read_bytes()).hexdigest(),
        )
        self.assertGreater(report["network_requests"], 0)
        self.assertTrue(report["invariants"]["max_two_sources"])
        self.assertTrue(report["invariants"]["max_two_concurrent_reads"])
        self.assertTrue(report["invariants"]["output_within_cap"])
        self.assertEqual(report["metrics"]["peak_concurrency_max"], 2)
        self.assertLessEqual(report["metrics"]["auto_fetch_targets_max"], 2)
        self.assertGreater(report["metrics"]["cases_with_auto_fetch"], 0)
        self.assertNotIn("conditional_auto_fetch", report["not_measured"])

    async def test_auto_fetch_replay_row_shape(self) -> None:
        case = load_cases()[0]
        row = await replay_auto_fetch(case)
        self.assertLessEqual(row["auto_fetch_targets"], 2)
        self.assertLessEqual(row["peak_concurrency"], 2)
        self.assertTrue(row["output_within_cap"])


if __name__ == "__main__":
    unittest.main()
