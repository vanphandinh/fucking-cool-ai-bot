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
        self.assertIn('CRAWL4AI_UPSTREAM_PROXY: http://crawl4ai-proxy:3128', self.block)
        for forbidden in (
            "OPENAI_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "XKIRO_API_" + "KEY",
            "CLOUDFLARE_API_TOKEN",
        ):
            self.assertNotIn(forbidden, self.block)

    def test_isolated_network_topology_and_squid_service(self):
        self.assertIn("crawl4ai_api", self.text)
        self.assertIn("crawl4ai_proxy", self.text)
        self.assertIn("crawl4ai_uplink", self.text)
        self.assertIn('name: fcai-crawl4ai-api', self.text)
        self.assertIn('name: fcai-crawl4ai-proxy', self.text)
        self.assertIn('name: fcai-crawl4ai-uplink', self.text)
        self.assertIn('ubuntu/squid:6.10-24.10_edge@sha256:', self.text)
        self.assertNotIn("ports:", self.text)
        self.assertIn("internal: true", self.text)
        squid = Path("crawl4ai/squid.conf").read_text(encoding="utf-8")
        self.assertIn("http_access deny forbidden_dst", squid)
        self.assertIn("http_access deny !Safe_ports", squid)
        self.assertIn("acl SSL_ports port 443", squid)
        self.assertIn("http_access deny CONNECT !SSL_ports", squid)


if __name__ == "__main__":
    unittest.main()
