from __future__ import annotations

from pathlib import Path
import unittest


def _aurora_block() -> str:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    marker = "\n  aurora:\n"
    if marker not in compose:
        return ""
    return compose.split(marker, 1)[1].split("\nvolumes:", 1)[0]


class AuroraComposeTests(unittest.TestCase):
    def test_aurora_is_profiled_private_and_pinned(self) -> None:
        block = _aurora_block()
        self.assertTrue(block, "Aurora service block is missing")
        self.assertIn('profiles: ["aurora"]', block)
        self.assertIn("ghcr.io/aurora-develop/aurora:v2.6.3", block)
        self.assertNotIn("ports:", block)
        self.assertNotIn("8080:8080", block)

    def test_risky_gateway_features_are_disabled(self) -> None:
        block = _aurora_block()
        self.assertTrue(block, "Aurora service block is missing")
        self.assertIn('FREE_ACCOUNTS: "false"', block)
        self.assertIn('ENABLE_EXTERNAL_TOKEN: "false"', block)
        self.assertIn('ENABLE_HISTORY: "false"', block)
        self.assertIn('TOOL_CALLING_ENABLED: "true"', block)
        self.assertIn('STREAM_MODE: "false"', block)
        self.assertIn('REFUSAL_RETRIES: "3"', block)

    def test_chatgpt_credentials_are_writable_files_not_env_values(self) -> None:
        block = _aurora_block()
        self.assertTrue(block, "Aurora service block is missing")
        env = Path(".env.example").read_text(encoding="utf-8")
        gitignore = Path(".gitignore").read_text(encoding="utf-8")
        self.assertIn("AURORA_CREDENTIAL_FILE", block)
        self.assertIn("AURORA_CREDENTIAL_TARGET", block)
        self.assertIn("read_only: false", block)
        self.assertNotIn("read_only: true", block)
        self.assertNotIn("SESSION_TOKEN=", env)
        self.assertNotIn("REFRESH_TOKEN=", env)
        self.assertNotIn("ACCESS_TOKEN=", env)
        self.assertIn("aurora/session_tokens.txt", gitignore)
        self.assertIn("aurora/refresh_tokens.txt", gitignore)
        self.assertIn("aurora/access_tokens.txt", gitignore)

    def test_session_token_mount_targets_aurora_nonroot_home(self) -> None:
        block = _aurora_block()

        self.assertIn(
            "target: ${AURORA_CREDENTIAL_TARGET:-/home/nonroot/session_tokens.txt}",
            block,
        )

        self.assertNotIn(
            "target: ${AURORA_CREDENTIAL_TARGET:-/session_tokens.txt}",
            block,
        )


if __name__ == "__main__":
    unittest.main()
