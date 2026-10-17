"""Source-grounding guidance for the only web evidence policy."""

import unittest

from app.config import Settings
from app.core.orchestrator import Orchestrator


class SourceGroundingPromptTests(unittest.TestCase):
    def setUp(self):
        self.orchestrator = Orchestrator(Settings(_env_file=None), None)
        self.prompt = self.orchestrator.system_prompt()

    def test_guides_false_claim_verification_without_hardcoding_answer(self):
        self.assertIn("Tách tuyên bố thành những phần", self.prompt)
        self.assertIn("không tự bổ sung một sự kiện", self.prompt)
        self.assertIn("nguồn mâu thuẫn", self.prompt)
        self.assertIn("năm hiện tại và trang chính thức", self.prompt)
        self.assertIn("hãy tìm thêm bằng web_search", self.prompt)
        self.assertIn("Chỉ dẫn URL và liên kết đã xuất hiện trong kết quả", self.prompt)
        self.assertNotIn("Artemis", self.prompt)

    def test_forbids_citing_urls_outside_tool_results_without_hardcoding(self):
        """A link the model never retrieved must not be presented as a source.

        Two of the eight audited live answers cited official-looking URLs that
        were absent from the recorded results, so the rule is generic.
        """
        self.assertIn("không nhớ lại liên kết chưa truy", self.prompt)
        self.assertIn("liên kết phỏng đoán", self.prompt)
        self.assertNotIn("europa.eu", self.prompt)
        self.assertNotIn("gov.uk", self.prompt)

    def test_guidance_is_present_without_any_flag_or_request_state(self):
        self.assertIn("KHI KIỂM CHỨNG THÔNG TIN TỪ WEB", self.prompt)
        self.assertEqual(self.prompt, self.orchestrator.system_prompt())


if __name__ == "__main__":
    unittest.main()
