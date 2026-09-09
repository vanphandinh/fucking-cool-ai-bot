from __future__ import annotations

import unittest
from pathlib import Path


class Crawl4AIComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = Path("docker-compose.yml").read_text(encoding="utf-8")
        marker = "\n  crawl4ai:\n"
        cls.assertIn(cls, marker, cls.text)
        cls.block = cls.text.split(marker, 1)[1].split("\nvolumes:", 1)[0]

    def test_crawl4ai_is_pinned_private_and_profiled(self):
        self.assertIn("unclecode/crawl4ai:0.9.3", self.block)
        self.assertIn('profiles: ["crawl4ai"]', self.block)
        self.assertNotIn("11235:11235", self.block)
        self.assertNotIn("ports:", self.block)
        self.assertIn("/health", self.block)

    def test_crawl4ai_requires_token_and_disables_risky_features(self):
        self.assertIn("CRAWL4AI_API_TOKEN", self.block)
        self.assertIn('CRAWL4AI_EXECUTE_JS_ENABLED: "false"', self.block)
        self.assertIn('CRAWL4AI_HOOKS_ENABLED: "false"', self.block)
        for forbidden in (
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "BAI_API_KEY",
            "CLOUDFLARE_API_TOKEN",
        ):
            self.assertNotIn(forbidden, self.block)


if __name__ == "__main__":
    unittest.main()
