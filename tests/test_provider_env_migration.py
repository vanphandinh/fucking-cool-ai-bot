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
    def _run(self, directory: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_repo_template_uses_chainnode_primary_and_xkiro_fallback(self) -> None:
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("CHAINNODE_API_KEY=", template)
        self.assertIn("CHAINNODE_BASE_URL=https://dn.chainno.de/v1", template)
        self.assertIn(
            "CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash",
            template,
        )
        self.assertIn(
            "CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor",
            template,
        )
        self.assertIn("CHAINNODE_REQUEST_TIMEOUT_SEC=60.0", template)
        self.assertIn("XKIRO_API_KEY=", template)
        self.assertIn("XKIRO_TEXT_MODEL=\n", template)
        self.assertIn("XKIRO_VISION_MODEL=\n", template)
        self.assertIn("XKIRO_REQUEST_TIMEOUT_SEC=60.0", template)
        self.assertIn("TEXT_PROVIDER_ORDER=chainnode,xkiro", template)
        self.assertIn("VISION_PROVIDER_ORDER=chainnode,xkiro", template)
        for removed in (
            "BAI_API_KEY",
            "BAI_TEXT_MODEL",
            "BAI_VISION_MODEL",
            "BAI_REQUEST_TIMEOUT_SEC",
        ):
            self.assertNotIn(removed, template)

    def test_sync_migrates_legacy_orders_and_removes_bai_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash\n"
                "CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "XKIRO_VISION_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_PROVIDER_ORDER=chainnode,xkiro\n"
                "SEARCH_BACKEND=auto\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "CHAINNODE_API_KEY=chainnode-existing\n"
                "CHAINNODE_TEXT_MODEL=chainnode-text-existing\n"
                "XKIRO_API_KEY=xkiro-existing\n"
                "XKIRO_TEXT_MODEL=xkiro-text-existing\n"
                "XKIRO_VISION_MODEL=xkiro-vision-existing\n"
                "BAI_API_KEY=legacy-bai-value\n"
                "BAI_TEXT_MODEL=legacy-text\n"
                "BAI_VISION_MODEL=legacy-vision\n"
                "BAI_REQUEST_TIMEOUT_SEC=17\n"
                "TEXT_PROVIDER_ORDER=bai,chainnode,custom\n"
                "VISION_PROVIDER_ORDER=chainnode,bai\n"
                "SEARCH_BACKEND=searxng\n",
                encoding="utf-8",
            )

            result = self._run(directory)
            rendered = (directory / ".env").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CHAINNODE_API_KEY=chainnode-existing", rendered)
        self.assertIn("CHAINNODE_TEXT_MODEL=chainnode-text-existing", rendered)
        self.assertIn("XKIRO_API_KEY=xkiro-existing", rendered)
        self.assertIn("XKIRO_TEXT_MODEL=xkiro-text-existing", rendered)
        self.assertIn("XKIRO_VISION_MODEL=xkiro-vision-existing", rendered)
        self.assertIn("TEXT_PROVIDER_ORDER=chainnode,xkiro,custom", rendered)
        self.assertIn("VISION_PROVIDER_ORDER=chainnode,xkiro", rendered)
        self.assertIn("SEARCH_BACKEND=searxng", rendered)
        self.assertNotIn("legacy-bai-value", rendered)
        self.assertNotIn("BAI_", rendered)

    def test_sync_migrates_quoted_and_commented_legacy_orders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "CHAINNODE_API_KEY=chainnode-existing\n"
                "CHAINNODE_TEXT_MODEL=chainnode-text-existing\n"
                'TEXT_PROVIDER_ORDER="bai,chainnode"\n'
                "VISION_PROVIDER_ORDER='bai' # legacy quoted value\n",
                encoding="utf-8",
            )

            result = self._run(directory)
            rendered = (directory / ".env").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TEXT_PROVIDER_ORDER=chainnode,xkiro", rendered)
        self.assertIn("VISION_PROVIDER_ORDER=chainnode,xkiro", rendered)
        self.assertNotIn("'bai'", rendered)
        self.assertNotIn('"bai,chainnode"', rendered)

    def test_sync_rejects_unclosed_provider_order_quote_without_touching_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            original = (
                "CHAINNODE_API_KEY=chainnode-existing\n"
                "CHAINNODE_TEXT_MODEL=chainnode-text-existing\n"
                'TEXT_PROVIDER_ORDER="bai,chainnode\n'
            )
            env_path = directory / ".env"
            env_path.write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("provider order", result.stderr.lower())
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

    def test_sync_does_not_invent_xkiro_credentials_from_bai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "XKIRO_VISION_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "CHAINNODE_API_KEY=chainnode-existing\n"
                "CHAINNODE_TEXT_MODEL=chainnode-text-existing\n"
                "BAI_API_KEY=legacy-bai-value\n"
                "BAI_TEXT_MODEL=legacy-text\n"
                "TEXT_PROVIDER_ORDER=bai\n",
                encoding="utf-8",
            )

            result = self._run(directory)
            rendered = (directory / ".env").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CHAINNODE_API_KEY=chainnode-existing", rendered)
        self.assertIn("CHAINNODE_TEXT_MODEL=chainnode-text-existing", rendered)
        self.assertIn("XKIRO_API_KEY=\n", rendered)
        self.assertIn("XKIRO_TEXT_MODEL=\n", rendered)
        self.assertIn("TEXT_PROVIDER_ORDER=chainnode,xkiro", rendered)
        self.assertNotIn("legacy-bai-value", rendered)
        self.assertNotIn("legacy-text", rendered)

    def test_sync_refuses_bai_only_retirement_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            original = (
                "BAI_API_KEY=legacy-bai-value\n"
                "BAI_TEXT_MODEL=legacy-text\n"
                "TEXT_PROVIDER_ORDER=bai\n"
            )
            env_path = directory / ".env"
            env_path.write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("replacement text provider", result.stderr.lower())
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

    def test_sync_refuses_bai_retirement_when_legacy_model_used_runtime_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            original = (
                "BAI_API_KEY=legacy-bai-value\n"
                "TEXT_PROVIDER_ORDER=bai\n"
            )
            env_path = directory / ".env"
            env_path.write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("replacement text provider", result.stderr.lower())
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

    def test_sync_refuses_legacy_default_text_order_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            original = "BAI_API_KEY=legacy-bai-value\n"
            env_path = directory / ".env"
            env_path.write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("replacement text provider", result.stderr.lower())
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

    def test_sync_treats_quoted_empty_commented_replacement_keys_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=xkiro-text\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            original = (
                'CHAINNODE_API_KEY="" # intentionally empty\n'
                "CHAINNODE_TEXT_MODEL=chainnode-text\n"
                "XKIRO_API_KEY='' # intentionally empty\n"
                "XKIRO_TEXT_MODEL=xkiro-text\n"
                "BAI_API_KEY=legacy-bai-value\n"
                "BAI_TEXT_MODEL=legacy-text\n"
                "TEXT_PROVIDER_ORDER=bai\n"
            )
            env_path = directory / ".env"
            env_path.write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("replacement text provider", result.stderr.lower())
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

    def test_sync_refuses_enabled_legacy_vision_retirement_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=chainnode-text\n"
                "CHAINNODE_VISION_MODEL=chainnode-vision\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "XKIRO_VISION_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_ENABLED=1\n",
                encoding="utf-8",
            )
            original = (
                "XKIRO_API_KEY=xkiro-existing\n"
                "XKIRO_TEXT_MODEL=xkiro-text-existing\n"
                "XKIRO_VISION_MODEL=\n"
                "BAI_API_KEY=legacy-bai-value\n"
                "TEXT_PROVIDER_ORDER=bai\n"
                "VISION_PROVIDER_ORDER=bai\n"
                "VISION_ENABLED=1\n"
            )
            env_path = directory / ".env"
            env_path.write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("replacement vision provider", result.stderr.lower())
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

    def test_sync_refuses_legacy_default_vision_order_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=chainnode-text\n"
                "CHAINNODE_VISION_MODEL=chainnode-vision\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "XKIRO_VISION_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_ENABLED=1\n",
                encoding="utf-8",
            )
            original = (
                "XKIRO_API_KEY=xkiro-existing\n"
                "XKIRO_TEXT_MODEL=xkiro-text-existing\n"
                "XKIRO_VISION_MODEL=\n"
                "BAI_API_KEY=legacy-bai-value\n"
            )
            env_path = directory / ".env"
            env_path.write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("replacement vision provider", result.stderr.lower())
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

    def test_sync_allows_legacy_vision_retirement_when_vision_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=chainnode-text\n"
                "CHAINNODE_VISION_MODEL=chainnode-vision\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "XKIRO_VISION_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_ENABLED=1\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "XKIRO_API_KEY=xkiro-existing\n"
                "XKIRO_TEXT_MODEL=xkiro-text-existing\n"
                "XKIRO_VISION_MODEL=\n"
                "BAI_API_KEY=legacy-bai-value\n"
                "VISION_ENABLED=0\n",
                encoding="utf-8",
            )

            result = self._run(directory)
            rendered = (directory / ".env").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VISION_ENABLED=0", rendered)
        self.assertIn("VISION_PROVIDER_ORDER=chainnode,xkiro", rendered)

    def test_chainnode_only_custom_order_is_not_expanded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "TEXT_PROVIDER_ORDER=chainnode\n"
                "VISION_PROVIDER_ORDER=chainnode\n",
                encoding="utf-8",
            )

            result = self._run(directory)
            rendered = (directory / ".env").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            rendered,
            "TEXT_PROVIDER_ORDER=chainnode\nVISION_PROVIDER_ORDER=chainnode\n",
        )


if __name__ == "__main__":
    unittest.main()
