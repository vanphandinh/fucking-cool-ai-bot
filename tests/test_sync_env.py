from pathlib import Path
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "sync_env.py"


class SyncEnvTests(unittest.TestCase):
    def _run(self, directory: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_sync_preserves_existing_values_adds_new_keys_and_removes_deleted_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                textwrap.dedent(
                    """\
                    # section
                    KEEP=example-default
                    NEW=example-new
                    EMPTY=
                    """
                ),
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                textwrap.dedent(
                    """\
                    KEEP=existing-value
                    OLD=remove-me
                    EMPTY=keep-empty-override
                    """
                ),
                encoding="utf-8",
            )

            result = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / ".env").read_text(encoding="utf-8"),
                textwrap.dedent(
                    """\
                    # section
                    KEEP=existing-value
                    NEW=example-new
                    EMPTY=keep-empty-override
                    """
                ),
            )

    def test_legacy_bai_orders_migrate_to_chainnode_then_xkiro(self):
        cases = {
            "bai": "chainnode,xkiro",
            "chainnode,bai": "chainnode,xkiro",
            "bai,chainnode": "chainnode,xkiro",
            "chainnode,bai,foo": "chainnode,xkiro,foo",
            "foo,bai,chainnode,foo": "chainnode,xkiro,foo",
        }
        for key in ("TEXT_PROVIDER_ORDER", "VISION_PROVIDER_ORDER"):
            for raw, expected in cases.items():
                with self.subTest(key=key, raw=raw):
                    with tempfile.TemporaryDirectory() as tmp:
                        directory = Path(tmp)
                        (directory / ".env.example").write_text(
                            f"{key}=chainnode,xkiro\n",
                            encoding="utf-8",
                        )
                        (directory / ".env").write_text(
                            f"{key}={raw}\n",
                            encoding="utf-8",
                        )

                        result = self._run(directory)

                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(
                            (directory / ".env").read_text(encoding="utf-8"),
                            f"{key}={expected}\n",
                        )

    def test_provider_order_without_bai_is_preserved_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "TEXT_PROVIDER_ORDER=Foo,chainnode\n",
                encoding="utf-8",
            )

            result = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / ".env").read_text(encoding="utf-8"),
                "TEXT_PROVIDER_ORDER=Foo,chainnode\n",
            )

    def test_bai_credentials_are_removed_and_never_copied_to_xkiro(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODEL=chainnode-default\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "CHAINNODE_API_KEY=chainnode-existing\n"
                "CHAINNODE_TEXT_MODEL=chainnode-existing-model\n"
                "BAI_API_KEY=legacy-bai-value\n"
                "BAI_TEXT_MODEL=legacy-bai-model\n"
                "TEXT_PROVIDER_ORDER=bai\n",
                encoding="utf-8",
            )

            result = self._run(directory)
            rendered = (directory / ".env").read_text(encoding="utf-8")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("CHAINNODE_API_KEY=chainnode-existing", rendered)
            self.assertIn("CHAINNODE_TEXT_MODEL=chainnode-existing-model", rendered)
            self.assertIn("XKIRO_API_KEY=\n", rendered)
            self.assertIn("XKIRO_TEXT_MODEL=\n", rendered)
            self.assertIn("TEXT_PROVIDER_ORDER=chainnode,xkiro", rendered)
            self.assertNotIn("BAI_API_KEY", rendered)
            self.assertNotIn("BAI_TEXT_MODEL", rendered)
            self.assertNotIn("legacy-bai-value", rendered)

    def test_existing_xkiro_values_survive_migration_and_second_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            env_path = directory / ".env"
            (directory / ".env.example").write_text(
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "XKIRO_VISION_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            env_path.write_text(
                "XKIRO_API_KEY=xkiro-existing\n"
                "XKIRO_TEXT_MODEL=text-existing\n"
                "XKIRO_VISION_MODEL=vision-existing\n"
                "TEXT_PROVIDER_ORDER=chainnode,bai\n"
                "VISION_PROVIDER_ORDER=bai,chainnode\n",
                encoding="utf-8",
            )

            first = self._run(directory)
            expected = (
                "XKIRO_API_KEY=xkiro-existing\n"
                "XKIRO_TEXT_MODEL=text-existing\n"
                "XKIRO_VISION_MODEL=vision-existing\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n"
                "VISION_PROVIDER_ORDER=chainnode,xkiro\n"
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), expected)

            fixed_ns = 1_600_000_000_000_000_000
            os.utime(env_path, ns=(fixed_ns, fixed_ns))
            second = self._run(directory)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), expected)
            self.assertEqual(env_path.stat().st_mtime_ns, fixed_ns)

    def test_provider_retry_legacy_names_migrate_and_second_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            example = (
                "PROVIDER_RETRY_MAX_CONSECUTIVE=2\n"
                "PROVIDER_RETRY_MAX_PER_PROVIDER=3\n"
                "PROVIDER_RETRY_MAX_PER_REQUEST=5\n"
            )
            env_path = directory / ".env"
            (directory / ".env.example").write_text(example, encoding="utf-8")
            env_path.write_text(
                "PROVIDER_RETRY_MAX_CONSECUTIVE_FAILURES=3\n"
                "PROVIDER_RETRY_MAX_FAILURES_PER_PROVIDER=7\n"
                "PROVIDER_RETRY_MAX_FAILURES_PER_REQUEST=9\n",
                encoding="utf-8",
            )

            first = self._run(directory)

            self.assertEqual(first.returncode, 0, first.stderr)
            expected = (
                "PROVIDER_RETRY_MAX_CONSECUTIVE=3\n"
                "PROVIDER_RETRY_MAX_PER_PROVIDER=7\n"
                "PROVIDER_RETRY_MAX_PER_REQUEST=9\n"
            )
            self.assertEqual(env_path.read_text(encoding="utf-8"), expected)

            fixed_ns = 1_600_000_000_000_000_000
            os.utime(env_path, ns=(fixed_ns, fixed_ns))
            second = self._run(directory)

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), expected)
            self.assertEqual(env_path.stat().st_mtime_ns, fixed_ns)

    def test_provider_retry_canonical_names_win_over_legacy_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "PROVIDER_RETRY_MAX_CONSECUTIVE=2\n"
                "PROVIDER_RETRY_MAX_PER_PROVIDER=3\n"
                "PROVIDER_RETRY_MAX_PER_REQUEST=5\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "PROVIDER_RETRY_MAX_CONSECUTIVE=4\n"
                "PROVIDER_RETRY_MAX_CONSECUTIVE_FAILURES=1\n"
                "PROVIDER_RETRY_MAX_PER_PROVIDER=8\n"
                "PROVIDER_RETRY_MAX_FAILURES_PER_PROVIDER=6\n"
                "PROVIDER_RETRY_MAX_PER_REQUEST=10\n"
                "PROVIDER_RETRY_MAX_FAILURES_PER_REQUEST=7\n",
                encoding="utf-8",
            )

            result = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / ".env").read_text(encoding="utf-8"),
                "PROVIDER_RETRY_MAX_CONSECUTIVE=4\n"
                "PROVIDER_RETRY_MAX_PER_PROVIDER=8\n"
                "PROVIDER_RETRY_MAX_PER_REQUEST=10\n",
            )

    def test_duplicate_key_in_example_fails_without_touching_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text("A=1\nA=2\n", encoding="utf-8")
            original = "A=value\n"
            (directory / ".env").write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate key", result.stderr.lower())
            self.assertNotIn("traceback", result.stderr.lower())
            self.assertEqual((directory / ".env").read_text(encoding="utf-8"), original)

    def test_duplicate_key_in_current_env_fails_without_touching_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text("A=default\n", encoding="utf-8")
            original = "A=first\nA=second\n"
            (directory / ".env").write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate key", result.stderr.lower())
            self.assertEqual((directory / ".env").read_text(encoding="utf-8"), original)

    def test_invalid_assignment_in_example_fails_without_touching_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text("GOOD=1\nBAD LINE\n", encoding="utf-8")
            original = "GOOD=value\n"
            (directory / ".env").write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid assignment", result.stderr.lower())
            self.assertEqual((directory / ".env").read_text(encoding="utf-8"), original)

    def test_invalid_assignment_in_current_env_fails_without_touching_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text("GOOD=1\n", encoding="utf-8")
            original = "GOOD=value\nBAD LINE\n"
            (directory / ".env").write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid assignment", result.stderr.lower())
            self.assertEqual((directory / ".env").read_text(encoding="utf-8"), original)

    def test_second_sync_is_idempotent_and_does_not_rewrite_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            content = "# section\nA=value\nB=default\n"
            (directory / ".env.example").write_text(
                "# section\nA=example\nB=default\n", encoding="utf-8"
            )
            env_path = directory / ".env"
            env_path.write_text(content, encoding="utf-8")
            fixed_ns = 1_600_000_000_000_000_000
            os.utime(env_path, ns=(fixed_ns, fixed_ns))

            result = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), content)
            self.assertEqual(env_path.stat().st_mtime_ns, fixed_ns)

    def test_missing_env_is_created_from_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            example = "# section\nA=default\nEMPTY=\n"
            (directory / ".env.example").write_text(example, encoding="utf-8")

            result = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((directory / ".env").read_text(encoding="utf-8"), example)

    def test_existing_values_with_equals_quotes_hashes_and_spaces_are_preserved_exactly(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "TOKEN=default\nJSON=default\nSPACED=default\n", encoding="utf-8"
            )
            (directory / ".env").write_text(
                'TOKEN=abc=def==\nJSON={"a":"b=c#d"}\nSPACED=  keep me  \n',
                encoding="utf-8",
            )

            result = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (directory / ".env").read_text(encoding="utf-8"),
                'TOKEN=abc=def==\nJSON={"a":"b=c#d"}\nSPACED=  keep me  \n',
            )


if __name__ == "__main__":
    unittest.main()
