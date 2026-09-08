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

    def test_prompt_requests_telegram_native_html(self):
        self.assertIn("telegram", self.prompt)
        self.assertIn("html", self.prompt)
        for tag in ("<b>", "<i>", "<code>", "<pre>", "<blockquote>"):
            with self.subTest(tag=tag):
                self.assertIn(tag, self.prompt)

    def test_prompt_forbids_raw_markdown_and_format_overuse(self):
        self.assertIn("không dùng markdown", self.prompt)
        self.assertIn("không lạm dụng", self.prompt)
        self.assertIn("đoạn ngắn", self.prompt)
        self.assertIn("bullet", self.prompt)

    def test_prompt_keeps_sources_out_of_main_answer(self):
        self.assertIn("hệ thống tự đính nguồn", self.prompt)
        self.assertIn("không cần liệt kê nguồn", self.prompt)

    def test_direct_url_is_fetched_before_web_search(self):
        self.assertIn("fetch_url", self.prompt)
        self.assertIn("url cụ thể", self.prompt)
        self.assertIn("trước web_search", self.prompt)

    def test_x_direct_url_policy_avoids_mirror_searches(self):
        self.assertIn("x/twitter", self.prompt)
        self.assertIn("không tìm mirror", self.prompt)

    def test_fetch_url_tool_supports_x_thread_mode(self):
        fetch_tool = next(
            tool["function"] for tool in TOOLS if tool["function"]["name"] == "fetch_url"
        )
        mode = fetch_tool["parameters"]["properties"]["mode"]
        self.assertEqual(mode["enum"], ["auto", "x_thread"])
        self.assertIn("x/thread", fetch_tool["description"].lower())


if __name__ == "__main__":
    unittest.main()
