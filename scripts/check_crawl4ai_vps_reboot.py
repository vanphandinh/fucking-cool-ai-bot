"""Record and verify the Crawl4AI firewall across an actual Linux VPS reboot.

On the VPS, with Crawl4AI disabled:
  sudo python3 scripts/check_crawl4ai_vps_reboot.py before
  # Reboot the VPS through the operator's normal procedure.
  sudo python3 scripts/check_crawl4ai_vps_reboot.py after

This script does not reboot the host, modify firewall rules or enable Crawl4AI.
Both phases run the non-disruptive live egress probes. A container restart or
Docker daemon restart without a host reboot cannot pass the after phase.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
DEFAULT_STATE = Path("/var/tmp/fcai-crawl4ai-reboot-state.json")


def command(*argv: str, timeout: int = 150) -> str:
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                            check=False, timeout=timeout)
    if result.returncode:
        # Never print environment values or URLs supplied to the probes.
        raise RuntimeError(f"{argv[0]} exited with status {result.returncode}")
    return result.stdout.strip()


def verify_host() -> tuple[str, str]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("root on the actual Linux VPS is required")
    docker_os = command("docker", "info", "--format", "{{.OperatingSystem}}")
    if "docker desktop" in docker_os.lower():
        raise RuntimeError("Docker Desktop cannot certify VPS reboot persistence")
    boot_id = BOOT_ID.read_text(encoding="ascii").strip()
    if not boot_id:
        raise RuntimeError("cannot read Linux host boot ID")
    revision = command("git", "rev-parse", "HEAD")
    if len(revision) != 40:
        raise RuntimeError("cannot identify the checked-out Git revision")
    return boot_id, revision


def verify_firewall_and_live_egress() -> None:
    command("bash", str(ROOT / "scripts" / "crawl4ai_firewall.sh"), "check")
    print("PASS: actual VPS firewall rules present")
    report = command(sys.executable,
                     str(ROOT / "scripts" / "check_crawl4ai_vps_live.py"),
                     "--live", timeout=150)
    if "LIVE EGRESS PASS" not in report:
        raise RuntimeError("live egress probe did not report success")
    print(report)


def save_state(path: Path, state: dict[str, object]) -> None:
    # Write a root-owned, private report atomically, without exposing tokens.
    fd, temporary = tempfile.mkstemp(prefix=".fcai-reboot-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(state, output, indent=2, sort_keys=True)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_state(path: Path) -> dict[str, object]:
    # Do not follow a symlink to a privileged file in /var/tmp.
    # Linux has O_NOFOLLOW; allow the same code to be unit-tested on Windows.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        if sys.platform == "linux":
            metadata = os.fstat(source.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                    or metadata.st_mode & 0o077):
                raise RuntimeError("reboot report must be a private root-owned file")
        state = json.load(source)
    if not isinstance(state, dict) or state.get("version") != 1:
        raise RuntimeError("invalid reboot preflight report")
    return state


def check_reboot(state: dict[str, object], boot_id: str, revision: str) -> None:
    if state.get("boot_id") == boot_id:
        raise RuntimeError("Linux host has not rebooted since the before phase")
    if not isinstance(state.get("boot_id"), str) or not state["boot_id"]:
        raise RuntimeError("missing pre-reboot host boot ID")
    if state.get("revision") != revision:
        raise RuntimeError("Git revision changed between the reboot probes")
    if state.get("preflight") != "PASS":
        raise RuntimeError("pre-reboot security checks were not successful")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before", "after"))
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    try:
        boot_id, revision = verify_host()
        if args.phase == "after":
            state = load_state(args.state_file)
            check_reboot(state, boot_id, revision)
        else:
            state = {
                "version": 1, "boot_id": boot_id, "revision": revision,
                "preflight": "PASS", "before_utc": datetime.now(timezone.utc).isoformat(),
            }
        verify_firewall_and_live_egress()
        if args.phase == "after":
            state["after_boot_id"] = boot_id
            state["after_utc"] = datetime.now(timezone.utc).isoformat()
            state["postflight"] = "PASS"
        save_state(args.state_file, state)
        print(f"REBOOT {args.phase.upper()} PASS: {args.state_file}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as exc:
        print(f"REBOOT {args.phase.upper()} INCOMPLETE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
