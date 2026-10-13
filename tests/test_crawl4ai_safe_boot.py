"""Exercise the operator's opt-in VPS restart policy update with isolated stubs."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(platform.system() == "Linux", "Linux boot shell required")
class TestCrawl4AISafeBoot(unittest.TestCase):
    def run_script(self, *, state: str = "running", partial: bool = False,
                   firewall_fail: bool = False, enabled: bool = False
                   ) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts" / "apply_crawl4ai_safe_boot.sh",
                         scripts / "apply_crawl4ai_safe_boot.sh")
            (root / ".env").write_text(
                f"CRAWL4AI_ENABLED={int(enabled)}\n", encoding="utf-8")
            (scripts / "crawl4ai_firewall.sh").write_text(
                "#!/bin/sh\n[ \"${FCAI_TEST_FIREWALL_FAIL:-0}\" = 0 ]\n", encoding="utf-8")
            (scripts / "verify_crawl4ai_security.sh").write_text(
                "#!/bin/sh\necho 'AUDIT_REPORT_READ_ONLY'\n", encoding="utf-8")

            bin_dir = root / "bin"
            bin_dir.mkdir()
            (bin_dir / "id").write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
            (bin_dir / "uname").write_text("#!/bin/sh\necho Linux\n", encoding="utf-8")
            (bin_dir / "docker").write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  exec|info) exit 0;;\n"
                "  inspect) case \"$3\" in\n"
                "    *RestartPolicy*) echo no;;\n"
                "    *State.Status*)\n"
                "      if [ \"${FCAI_TEST_PARTIAL:-0}\" = 1 ] && "
                "[ \"$4\" = fcai-crawl4ai-dns ]; then echo exited; "
                "else echo \"$FCAI_TEST_STATE\"; fi;;\n"
                "    *) exit 9;;\n"
                "  esac;;\n"
                "  update) printf '%s\\n' \"$3\" >> \"$FCAI_TEST_LOG\";;\n"
                "  *) exit 99;;\n"
                "esac\n", encoding="utf-8")
            for path in bin_dir.iterdir():
                path.chmod(0o755)

            update_log = root / "updates.log"
            env = dict(os.environ)
            env.update({
                "PATH": f"{bin_dir}:{env['PATH']}",
                "FCAI_TEST_LOG": str(update_log),
                "FCAI_TEST_STATE": state,
                "FCAI_TEST_PARTIAL": "1" if partial else "0",
                "FCAI_TEST_FIREWALL_FAIL": "1" if firewall_fail else "0",
            })
            result = subprocess.run(
                ["bash", str(scripts / "apply_crawl4ai_safe_boot.sh")],
                capture_output=True, text=True, timeout=10, env=env, check=False,
            )
            updates = update_log.read_text(encoding="utf-8").splitlines() \
                if update_log.exists() else []
            return result, updates

    def test_running_applies_all_policies_and_runs_report(self) -> None:
        result, updates = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(updates, ["fcai-crawl4ai", "fcai-crawl4ai-proxy",
                                   "fcai-crawl4ai-dns"])
        self.assertIn("AUDIT_REPORT_READ_ONLY", result.stdout)

    def test_dormant_can_apply_policies_without_firewall_probe(self) -> None:
        result, updates = self.run_script(state="exited", firewall_fail=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(updates), 3)

    def test_partial_startup_aborts_without_mutation(self) -> None:
        result, updates = self.run_script(partial=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(updates, [])

    def test_failed_firewall_aborts_without_mutation(self) -> None:
        result, updates = self.run_script(firewall_fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(updates, [])

    def test_enabled_bot_setting_aborts_without_mutation(self) -> None:
        result, updates = self.run_script(enabled=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(updates, [])


if __name__ == "__main__":
    unittest.main()
