"""Offline benchmark contract and deterministic V1 regression coverage."""

from __future__ import annotations

import unittest
from collections import Counter

from scripts.generate_web_evidence_fixtures import DESTINATION, main as generate_fixtures
from scripts.web_evidence_benchmark import CATEGORIES, evaluate, load_cases, replay


class WebEvidenceFixturesTests(unittest.TestCase):
    def test_generated_snapshots_match_checked_in_fixture(self) -> None:
        before = DESTINATION.read_bytes()
        generate_fixtures()
        self.assertEqual(DESTINATION.read_bytes(), before)

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


class WebEvidenceReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_buried_gold_is_not_reached_by_v1_reader(self) -> None:
        case = next(case for case in load_cases() if case["id"] == "buried-01")
        result = await replay(case)
        self.assertEqual(result["evidence_hits"], 0)
        self.assertFalse(result["answer_correct_proxy"])
        self.assertEqual(result["fetch_count"], 2)

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
            "answer_accuracy_extractive_proxy",
            "citation_precision_extractive_proxy",
            "unsupported_claim_rate_extractive_proxy",
        ):
            self.assertGreaterEqual(first["metrics"][name], 0)
            self.assertLessEqual(first["metrics"][name], 1)
        self.assertIn("real_model_answer_accuracy", first["not_measured"])


if __name__ == "__main__":
    unittest.main()
