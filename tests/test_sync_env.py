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
                    KEEP=real-secret
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
                    KEEP=real-secret
                    NEW=example-new
                    EMPTY=keep-empty-override
                    """
                ),
            )

    def test_bai_only_sync_preserves_bai_and_removes_external_ai_provider_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "BAI_API_KEY=\n"
                "BAI_TEXT_MODEL=qwen3.8-flash\n"
                "BAI_VISION_MODEL=qwen3.8-flash\n"
                "SEARCH_BACKEND=auto\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "BAI_API_KEY=real-bai-secret\n"
                "BAI_TEXT_MODEL=qwen3.8-flash\n"
                "GEMINI_API_KEY=old-gemini\n"
                "GROQ_API_KEY=old-groq\n"
                "OPENROUTER_API_KEY=old-openrouter\n"
                "CLOUDFLARE_ACCOUNT_ID=old-account\n"
                "CLOUDFLARE_API_TOKEN=old-token\n"
                "TEXT_PROVIDER_ORDER=bai,gemini,groq,cloudflare,openrouter\n"
                "VISION_PROVIDER_ORDER=bai,gemini\n"
                "SEARCH_BACKEND=searxng\n",
                encoding="utf-8",
            )

            result = self._run(directory)
            rendered = (directory / ".env").read_text(encoding="utf-8")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("BAI_API_KEY=real-bai-secret", rendered)
            self.assertIn("BAI_TEXT_MODEL=qwen3.8-flash", rendered)
            self.assertIn("BAI_VISION_MODEL=qwen3.8-flash", rendered)
            self.assertIn("SEARCH_BACKEND=searxng", rendered)
            for removed in (
                "GEMINI_API_KEY",
                "GROQ_API_KEY",
                "OPENROUTER_API_KEY",
                "CLOUDFLARE_ACCOUNT_ID",
                "CLOUDFLARE_API_TOKEN",
                "TEXT_PROVIDER_ORDER",
                "VISION_PROVIDER_ORDER",
            ):
                self.assertNotIn(removed, rendered)

    def test_duplicate_key_in_example_fails_without_touching_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text("A=1\nA=2\n", encoding="utf-8")
            original = "A=secret\n"
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
            original = "GOOD=secret\n"
            (directory / ".env").write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid assignment", result.stderr.lower())
            self.assertEqual((directory / ".env").read_text(encoding="utf-8"), original)

    def test_invalid_assignment_in_current_env_fails_without_touching_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text("GOOD=1\n", encoding="utf-8")
            original = "GOOD=secret\nBAD LINE\n"
            (directory / ".env").write_text(original, encoding="utf-8")

            result = self._run(directory)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid assignment", result.stderr.lower())
            self.assertEqual((directory / ".env").read_text(encoding="utf-8"), original)

    def test_second_sync_is_idempotent_and_does_not_rewrite_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            content = "# section\nA=secret\nB=default\n"
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
