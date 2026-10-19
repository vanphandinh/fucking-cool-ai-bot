"""Guard the live smoke questions against accidental Markdown heading leakage."""

import unittest
import asyncio
import json
import tempfile
from pathlib import Path

from scripts.web_evidence_live_smoke import load_questions, load_questions_json


class LiveSmokeQuestionTests(unittest.TestCase):
    def test_search_trace_keeps_only_bounded_query_url_and_publication_date(self) -> None:
        from scripts.web_evidence_live_smoke import run
        import argparse
        from unittest.mock import AsyncMock, patch

        class FakeProvider:
            model = "fake-model"

            async def aclose(self):
                return None

        class FakeRouter:
            providers = [FakeProvider()]

            def configured_provider_names(self, require_vision):
                return ("fake",)

        class FakeAnswer:
            text = "Answer"
            provider = "fake"
            usage = {}
            citation_diagnostics = None
            searched = True
            sources = []
            images = []
            fallbacks = ()

        calls = []

        async def fake_search(query, *args, **kwargs):
            calls.append(query)
            return [{
                "url": "https://example.org/current",
                "published_at": "2026-10-01",
                "snippet": "must not enter the search trace",
                "title": "must not enter the search trace",
            }]

        class FakeOrchestrator:
            def __init__(self, settings, router):
                pass

            async def ask(self, question):
                from app.core.orchestrator import search_service
                await search_service.search(question)
                return FakeAnswer()

        with (
            patch("scripts.web_evidence_live_smoke.Settings", return_value=object()),
            patch(
                "scripts.web_evidence_live_smoke.build_provider_router",
                return_value=FakeRouter(),
            ),
            patch("scripts.web_evidence_live_smoke.Orchestrator", FakeOrchestrator),
            patch(
                "app.core.orchestrator.search_service.search",
                new=AsyncMock(side_effect=fake_search),
            ),
            patch("scripts.web_evidence_live_smoke.close_search_runtimes", new=AsyncMock()),
            patch("scripts.web_evidence_live_smoke.close_crawl4ai_client", new=AsyncMock()),
            patch("app.search.runtime.close_search_runtimes", new=AsyncMock()),
            patch("app.search.crawl4ai_client.close_crawl4ai_client", new=AsyncMock()),
            patch("scripts.web_evidence_live_smoke._write") as write,
        ):
            args = argparse.Namespace(
                x_url="https://x.com/jack/status/20",
                timeout=10,
                output=Path("unused"),
            )
            records = [{
                "id": 1,
                "question": "verify current fact",
                "category": "test",
            }]
            self.assertEqual(asyncio.run(run(args, records)), 0)

        report = write.call_args.args[1]
        row = report["results"][0]
        self.assertEqual(len(row["search_trace"]), 1)
        trace = row["search_trace"][0]
        self.assertEqual(trace["query"], "verify current fact")
        self.assertEqual(
            trace["results"],
            [{"url": "https://example.org/current", "published_at": "2026-10-01"}],
        )
        self.assertNotIn("snippet", str(trace))
        self.assertNotIn("title", str(trace))

    def test_preflight_failure_stops_before_any_model_call(self) -> None:
        import argparse
        from unittest.mock import AsyncMock, patch

        from scripts.web_evidence_live_smoke import run

        args = argparse.Namespace(
            x_url="https://x.com/jack/status/20",
            timeout=10,
            output=Path("unused"),
        )
        records = [{"id": 1, "question": "q", "category": "test"}]
        with (
            patch(
                "scripts.web_evidence_live_smoke._search_preflight",
                new=AsyncMock(
                    return_value={
                        "ok": False,
                        "result_count": 0,
                        "error_type": "RoutedSearchError",
                        "elapsed_seconds": 0.1,
                    }
                ),
            ),
            patch("scripts.web_evidence_live_smoke.build_provider_router") as router_factory,
            patch("scripts.web_evidence_live_smoke._write") as write,
            patch("scripts.web_evidence_live_smoke.close_search_runtimes", new=AsyncMock()),
        ):
            self.assertEqual(asyncio.run(run(args, records)), 2)

        router_factory.assert_not_called()
        report = write.call_args.args[1]
        self.assertFalse(report["search_preflight"]["ok"])
        self.assertEqual(report["results"], [])

    def test_extended_question_fixture(self) -> None:
        path = Path("tests/fixtures/web_evidence_v2/live_questions_extended.json")
        content = json.loads(path.read_text(encoding="utf-8"))
        loaded = load_questions_json(path)
        self.assertEqual(len(loaded), 16)
        self.assertEqual(len({num for num, _ in loaded}), 16)
        self.assertEqual(len({row["category"] for row in content}), 16)
        for item in content:
            self.assertTrue(item["rubric"])

    def test_custom_question_ids_are_rejected_when_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "questions.json"
            path.write_text(json.dumps([
                {"id": 1, "question": "a"}, {"id": 1, "question": "b"},
            ]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                load_questions_json(path)

    def test_questions_match_guide_without_group_descriptions(self) -> None:
        questions = load_questions("https://x.com/jack/status/20")
        self.assertEqual([num for num, _ in questions], list(range(1, 22)))
        for _, question in questions:
            self.assertNotIn("Nhóm ", question)
            self.assertNotIn("liệu, không bịa", question)
        self.assertEqual(
            questions[14][1],
            "Sự kiện công nghệ lớn nào diễn ra trong tháng này (tháng hiện tại của bạn)?",
        )

    def test_direct_urls_are_plain_and_x_url_is_customizable(self) -> None:
        x_url = "https://x.com/example/status/123"
        questions = dict(load_questions(x_url))
        self.assertIn("https://example.com", questions[19])
        self.assertNotIn("[https://example.com]", questions[19])
        self.assertIn(x_url, questions[21])


if __name__ == "__main__":
    unittest.main()
