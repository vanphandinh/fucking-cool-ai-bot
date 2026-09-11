"""Provider-order environment migration regressions."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "sync_env.py"


class ProviderEnvMigrationTests(unittest.TestCase):
    def test_repo_template_keeps_generic_provider_orders(self) -> None:
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("CHAINNODE_API_KEY=", template)
        self.assertIn("CHAINNODE_BASE_URL=https://dn.chainno.de/v1", template)
        self.assertIn("CHAINNODE_TEXT_MODEL=\n", template)
        self.assertIn("CHAINNODE_REQUEST_TIMEOUT_SEC=60.0", template)
        self.assertIn("TEXT_PROVIDER_ORDER=bai", template)
        self.assertIn("VISION_PROVIDER_ORDER=bai", template)

    def test_sync_preserves_orders_and_removes_deleted_provider_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "BAI_API_KEY=\n"
                "BAI_TEXT_MODEL=qwen3.8-flash\n"
                "BAI_VISION_MODEL=qwen3.8-flash\n"
                "TEXT_PROVIDER_ORDER=bai\n"
                "VISION_PROVIDER_ORDER=bai\n"
                "SEARCH_BACKEND=auto\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "BAI_API_KEY=real-bai-secret\n"
                "BAI_TEXT_MODEL=mimo-v2.5\n"
                "BAI_VISION_MODEL=qwen3.8-flash\n"
                "TEXT_PROVIDER_ORDER=bai,gemini\n"
                "VISION_PROVIDER_ORDER=bai,groq\n"
                "GEMINI_API_KEY=remove-gemini\n"
                "GROQ_API_KEY=remove-groq\n"
                "OPENROUTER_API_KEY=remove-openrouter\n"
                "CLOUDFLARE_API_TOKEN=remove-cloudflare\n"
                "SEARCH_BACKEND=searxng\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            rendered = (directory / ".env").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BAI_API_KEY=real-bai-secret", rendered)
        self.assertIn("BAI_TEXT_MODEL=mimo-v2.5", rendered)
        self.assertIn("TEXT_PROVIDER_ORDER=bai,gemini", rendered)
        self.assertIn("VISION_PROVIDER_ORDER=bai,groq", rendered)
        self.assertIn("SEARCH_BACKEND=searxng", rendered)
        for removed in (
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "OPENROUTER_API_KEY",
            "CLOUDFLARE_API_TOKEN",
        ):
            self.assertNotIn(removed, rendered)


if __name__ == "__main__":
    unittest.main()
