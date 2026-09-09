"""Regression tests for the B.AI-only routing defaults."""

from __future__ import annotations

import unittest

from app.config import Settings


class BaiOnlyDefaultsTests(unittest.TestCase):
    def test_defaults_keep_qwen_and_existing_limits(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.bai_text_model, "qwen3.8-flash")
        self.assertEqual(settings.bai_vision_model, "qwen3.8-flash")
        self.assertEqual(settings.max_images_per_request, 1)
        self.assertEqual(settings.max_context_turns, 6)
        self.assertEqual(settings.max_tool_rounds, 2)

    def test_stale_external_provider_env_fields_are_ignored(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="b",
            gemini_api_key="legacy",
            groq_api_key="legacy",
            openrouter_api_key="legacy",
            cloudflare_account_id="legacy",
            cloudflare_api_token="legacy",
            text_provider_order="legacy",
            vision_provider_order="legacy",
        )
        self.assertEqual(settings.configured_provider_names, ["bai"])
        self.assertEqual(settings.configured_vision_provider_names, ["bai"])


if __name__ == "__main__":
    unittest.main()
