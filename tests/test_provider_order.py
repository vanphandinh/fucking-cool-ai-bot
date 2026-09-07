"""Provider order configuration regressions."""

from __future__ import annotations

import unittest

from app.config import Settings


class ProviderOrderTests(unittest.TestCase):
    def test_effective_provider_orders_remove_duplicate_slots(self) -> None:
        settings = Settings(
            _env_file=None,
            groq_api_key="g",
            gemini_api_key="m",
            cloudflare_account_id="account",
            cloudflare_api_token="token",
            text_provider_order="groq,groq,cloudflare,groq,gemini,gemini",
            vision_provider_order=(
                "groq_qwen38,cloudflare,groq_qwen38,gemini,cloudflare"
            ),
        )

        self.assertEqual(
            settings.configured_provider_names,
            ["groq", "cloudflare", "gemini"],
        )
        self.assertEqual(
            settings.configured_vision_provider_names,
            ["groq_qwen38", "cloudflare", "gemini"],
        )
        self.assertEqual(
            settings.text_provider_order_list,
            ["groq", "cloudflare", "gemini"],
        )
        self.assertEqual(
            settings.vision_provider_order_list,
            ["groq_qwen38", "cloudflare", "gemini"],
        )


if __name__ == "__main__":
    unittest.main()
