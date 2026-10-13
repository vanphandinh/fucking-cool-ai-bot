"""Exercise the real boot entrypoint with isolated Docker/systemctl stubs."""

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
class TestCrawl4AIBootPolicy(unittest.TestCase):
    def run_boot(self, *, running: str, restart: str,
                 missing: bool = False) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            scripts = workspace / "scripts"
            scripts.mkdir()
            shutil.copy2(ROOT / "scripts" / "install_crawl4ai_firewall_boot.sh",
                         scripts / "install_crawl4ai_firewall_boot.sh")
            (workspace / ".env").write_text("CRAWL4AI_ENABLED=0\n", encoding="utf-8")
            (scripts / "crawl4ai_firewall.sh").write_text(
                "#!/bin/bash\nexit 0\n", encoding="utf-8")
            bin_dir = workspace / "bin"
            bin_dir.mkdir()
            (bin_dir / "id").write_text("#!/bin/sh\necho 0\n", encoding="utf-8")
            (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  info|exec) exit 0;;\n"
                "  inspect) if [ \"$4\" = \"${MISSING_CONTAINER:-}\" ]; then exit 1; fi\n"
                "  case \"$3\" in\n"
                f"    *RestartPolicy*) echo '{restart}';;\n"
                f"    *) echo '{running}';;\n"
                "  esac;;\n"
                "  *) exit 99;;\n"
                "esac\n",
                encoding="utf-8",
            )
            for path in bin_dir.iterdir():
                path.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            if missing:
                env["MISSING_CONTAINER"] = "fcai-crawl4ai-dns"
            else:
                env.pop("MISSING_CONTAINER", None)
            return subprocess.run(
                ["bash", str(scripts / "install_crawl4ai_firewall_boot.sh"), "--boot"],
                capture_output=True, text=True, timeout=10, env=env, check=False,
            )

    def test_dormant_no_restart_is_safe(self) -> None:
        result = self.run_boot(running="false", restart="no")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dormant after boot", result.stdout)

    def test_legacy_restart_policy_cannot_claim_dormant_pass(self) -> None:
        result = self.run_boot(running="false", restart="unless-stopped")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("dormant after boot", result.stdout)

    def test_missing_container_cannot_claim_dormant_pass(self) -> None:
        result = self.run_boot(running="false", restart="no", missing=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("dormant after boot", result.stdout)

    def test_running_containers_reapply_firewall(self) -> None:
        result = self.run_boot(running="true", restart="no")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("firewall restored after boot: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
