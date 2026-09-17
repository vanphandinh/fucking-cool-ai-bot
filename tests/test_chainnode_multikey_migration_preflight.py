"""Chainnode plural-credential semantics in legacy-provider migration preflight."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "sync_env.py"


class ChainnodeMultiKeyMigrationPreflightTests(unittest.TestCase):
    def _run(self, directory: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_plural_chainnode_credentials_satisfy_legacy_text_replacement_preflight(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEYS=\n"
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODELS=chain-default\n"
                "CHAINNODE_TEXT_MODEL=chain-default\n"
                "XKIRO_API_KEYS=\n"
                "XKIRO_TEXT_MODELS=\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            env_path = directory / ".env"
            env_path.write_text(
                "CHAINNODE_API_KEYS=ChainOne,ChainTwo\n"
                "CHAINNODE_TEXT_MODELS=ChainModel\n"
                "BAI_API_KEY=legacy-bai\n"
                "BAI_TEXT_MODEL=legacy-model\n"
                "TEXT_PROVIDER_ORDER=bai\n",
                encoding="utf-8",
            )

            result = self._run(directory)
            rendered = env_path.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CHAINNODE_API_KEYS=ChainOne,ChainTwo", rendered)
        self.assertIn("CHAINNODE_TEXT_MODELS=ChainModel", rendered)
        self.assertIn("TEXT_PROVIDER_ORDER=chainnode,xkiro", rendered)
        self.assertNotIn("BAI_", rendered)
        self.assertNotIn("legacy-bai", rendered)

    def test_explicit_blank_chainnode_plural_does_not_fall_back_to_scalar_in_preflight(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEYS=\n"
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODELS=chain-default\n"
                "CHAINNODE_TEXT_MODEL=chain-default\n"
                "XKIRO_API_KEYS=\n"
                "XKIRO_TEXT_MODELS=\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            original = (
                "CHAINNODE_API_KEYS=\n"
                "CHAINNODE_API_KEY=LegacyChainSecret\n"
                "CHAINNODE_TEXT_MODELS=ChainModel\n"
                "BAI_API_KEY=legacy-bai\n"
                "BAI_TEXT_MODEL=legacy-model\n"
                "TEXT_PROVIDER_ORDER=bai\n"
            )
            env_path = directory / ".env"
            env_path.write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("replacement text provider", result.stderr.lower())
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
