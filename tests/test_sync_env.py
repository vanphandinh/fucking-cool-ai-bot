from pathlib import Path
import os
import subprocess
import sys
import tempfile
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

    def test_unknown_current_env_key_fails_without_touching_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "KEEP=example-default\nNEW=example-new\n",
                encoding="utf-8",
            )
            original = "KEEP=existing-value\nUNKNOWN_SETTING=keep-me\n"
            env_path = directory / ".env"
            env_path.write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertEqual(result.returncode, 2)
            self.assertIn("unknown", result.stderr.lower())
            self.assertIn("UNKNOWN_SETTING", result.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), original)

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
