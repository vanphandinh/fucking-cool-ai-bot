"""Request-scoped V2 source-grounding guidance and rollback behaviour."""

import unittest

from app.config import Settings
from app.core.orchestrator import Orchestrator


class SourceGroundingPromptTests(unittest.TestCase):
    def test_v2_guides_false_claim_verification_without_hardcoding_answer(self):
        settings = Settings(_env_file=None, web_evidence_v2_enabled=True)
        prompt = Orchestrator(settings, None).system_prompt()
        self.assertIn("Tách tuyên bố thành những phần", prompt)
        self.assertIn("không tự bổ sung một sự kiện", prompt)
        self.assertIn("nguồn mâu thuẫn", prompt)
        self.assertIn("năm hiện tại và trang chính thức", prompt)
        self.assertIn("hãy tìm thêm bằng web_search", prompt)
        self.assertIn("Chỉ dẫn URL và liên kết đã xuất hiện trong kết quả", prompt)
        self.assertNotIn("Artemis", prompt)

    def test_v2_forbids_citing_urls_outside_tool_results_without_hardcoding(self):
        """A link the model never retrieved must not be presented as a source.

        Two of the eight audited live answers cited official-looking URLs that
        were absent from the recorded results, so the rule is generic.
        """
        prompt = Orchestrator(
            Settings(_env_file=None, web_evidence_v2_enabled=True), None
        ).system_prompt()
        self.assertIn("không nhớ lại liên kết chưa truy", prompt)
        self.assertIn("liên kết phỏng đoán", prompt)
        self.assertNotIn("europa.eu", prompt)
        self.assertNotIn("gov.uk", prompt)

    def test_flag_off_keeps_original_prompt_and_rollback(self):
        settings = Settings(_env_file=None, web_evidence_v2_enabled=False)
        orchestrator = Orchestrator(settings, None)
        off_prompt = orchestrator.system_prompt()
        self.assertNotIn("KHI KIỂM CHỨNG THÔNG TIN TỪ WEB", off_prompt)
        self.assertNotIn("Chỉ dẫn URL và liên kết", off_prompt)

        settings.web_evidence_v2_enabled = True
        self.assertIn("KHI KIỂM CHỨNG THÔNG TIN TỪ WEB", orchestrator.system_prompt())
        settings.web_evidence_v2_enabled = False
        self.assertEqual(off_prompt, orchestrator.system_prompt())


if __name__ == "__main__":
    unittest.main()
