"""Provider-pool env migration regressions."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "sync_env.py"


class ProviderPoolMigrationTests(unittest.TestCase):
    def test_legacy_scalars_migrate_to_case_preserving_plural_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODELS=Default/Model\n"
                "CHAINNODE_TEXT_MODEL=Default/Model\n"
                "XKIRO_API_KEYS=\n"
                "XKIRO_TEXT_MODELS=\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "CHAINNODE_API_KEY=ChainKeyCase\n"
                "CHAINNODE_TEXT_MODEL=Legacy/ModelCase\n"
                "XKIRO_API_KEY=KeyCaseSensitive\n"
                "XKIRO_TEXT_MODEL=X/ModelCase\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
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
        self.assertIn("CHAINNODE_TEXT_MODELS=Legacy/ModelCase", rendered)
        self.assertIn("XKIRO_API_KEYS=KeyCaseSensitive", rendered)
        self.assertIn("XKIRO_TEXT_MODELS=X/ModelCase", rendered)
        self.assertIn("CHAINNODE_TEXT_MODEL=Legacy/ModelCase", rendered)
        self.assertIn("XKIRO_API_KEY=KeyCaseSensitive", rendered)


if __name__ == "__main__":
    unittest.main()
