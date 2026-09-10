from pathlib import Path
import os
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "sync_env.py"


class SyncEnvPermissionTests(unittest.TestCase):
    def _run(self, directory: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are required")
    def test_missing_env_is_created_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            example_path = directory / ".env.example"
            example_path.write_text("TOKEN=default\n", encoding="utf-8")
            os.chmod(example_path, 0o644)

            result = self._run(directory)
            env_path = directory / ".env"

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are required")
    def test_existing_world_readable_env_is_tightened_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            content = "TOKEN=secret\n"
            (directory / ".env.example").write_text("TOKEN=default\n", encoding="utf-8")
            env_path = directory / ".env"
            env_path.write_text(content, encoding="utf-8")
            os.chmod(env_path, 0o644)
            fixed_ns = 1_600_000_000_000_000_000
            os.utime(env_path, ns=(fixed_ns, fixed_ns))

            result = self._run(directory)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(env_path.read_text(encoding="utf-8"), content)
            self.assertEqual(env_path.stat().st_mtime_ns, fixed_ns)
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
