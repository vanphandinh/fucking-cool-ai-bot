"""Guard the live smoke questions against accidental Markdown heading leakage."""

import unittest
import json
import tempfile
from pathlib import Path

from scripts.web_evidence_live_smoke import load_questions, load_questions_json


class LiveSmokeQuestionTests(unittest.TestCase):
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
