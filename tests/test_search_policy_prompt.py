"""Regression tests for the AI-first web-search decision policy."""

import unittest
from types import SimpleNamespace

from app.config import Settings
from app.core.orchestrator import Orchestrator, TOOLS


class SearchPolicyPromptTests(unittest.TestCase):
    def setUp(self):
        settings = Settings(_env_file=None, bot_username="testbot")
        self.prompt = Orchestrator(settings, SimpleNamespace()).system_prompt().lower()
        self.web_search_description = next(
            tool["function"]["description"]
            for tool in TOOLS
            if tool["function"]["name"] == "web_search"
        ).lower()

    def test_bare_translate_defaults_to_vietnamese(self):
        self.assertIn("dịch", self.prompt)
        self.assertIn("tiếng việt", self.prompt)
        self.assertIn("không nêu ngôn ngữ đích", self.prompt)

    def test_supplied_content_transformations_should_not_search(self):
        self.assertIn("không dùng web_search", self.prompt)
        for intent in ("dịch", "tóm tắt", "viết lại", "sửa ngữ pháp"):
            with self.subTest(intent=intent):
                self.assertIn(intent, self.prompt)
        self.assertIn("nội dung người dùng đã cung cấp", self.prompt)

    def test_external_or_current_information_can_use_search(self):
        for intent in ("thông tin mới", "kiểm chứng", "tìm nguồn"):
            with self.subTest(intent=intent):
                self.assertIn(intent, self.prompt)

    def test_web_search_tool_description_matches_prompt_policy(self):
        self.assertIn("không dùng", self.web_search_description)
        self.assertIn("dịch", self.web_search_description)
        self.assertIn("nội dung người dùng đã cung cấp", self.web_search_description)


if __name__ == "__main__":
    unittest.main()
