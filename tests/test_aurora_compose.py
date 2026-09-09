from __future__ import annotations

from pathlib import Path
import unittest


class AuroraComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        marker = "\n  aurora:\n"
        cls.assertIn(cls, marker, cls.compose)
        cls.block = cls.compose.split(marker, 1)[1].split("\nvolumes:", 1)[0]
        cls.env = Path(".env.example").read_text(encoding="utf-8")
        cls.gitignore = Path(".gitignore").read_text(encoding="utf-8")

    def test_aurora_is_profiled_private_and_pinned(self) -> None:
        self.assertIn('profiles: ["aurora"]', self.block)
        self.assertIn("ghcr.io/aurora-develop/aurora:v2.6.3", self.block)
        self.assertNotIn("ports:", self.block)
        self.assertNotIn("8080:8080", self.block)

    def test_risky_gateway_features_are_disabled(self) -> None:
        self.assertIn('FREE_ACCOUNTS: "false"', self.block)
        self.assertIn('ENABLE_EXTERNAL_TOKEN: "false"', self.block)
        self.assertIn('ENABLE_HISTORY: "false"', self.block)
        self.assertIn('TOOL_CALLING_ENABLED: "true"', self.block)
        self.assertIn('STREAM_MODE: "false"', self.block)
        self.assertIn('REFUSAL_RETRIES: "3"', self.block)

    def test_chatgpt_credentials_are_read_only_files_not_env_values(self) -> None:
        self.assertIn("AURORA_CREDENTIAL_FILE", self.block)
        self.assertIn("AURORA_CREDENTIAL_TARGET", self.block)
        self.assertIn("read_only: true", self.block)
        self.assertNotIn("SESSION_TOKEN=", self.env)
        self.assertNotIn("REFRESH_TOKEN=", self.env)
        self.assertNotIn("ACCESS_TOKEN=", self.env)
        self.assertIn("aurora/session_tokens.txt", self.gitignore)
        self.assertIn("aurora/refresh_tokens.txt", self.gitignore)
        self.assertIn("aurora/access_tokens.txt", self.gitignore)


if __name__ == "__main__":
    unittest.main()
