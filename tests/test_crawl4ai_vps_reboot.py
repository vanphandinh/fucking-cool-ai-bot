"""Offline checks for the actual VPS reboot verification gate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_crawl4ai_vps_reboot as reboot


class RebootGateTests(unittest.TestCase):
    def test_post_reboot_accepts_only_verified_live_or_safe_dormant(self) -> None:
        outcomes = (
            ("RESULT: SAFE DORMANT (crawler, proxy and DNS are all stopped).", "SAFE_DORMANT"),
            ("RESULT: PASS for live VPS egress isolation", "LIVE_EGRESS"),
        )
        for report, expected in outcomes:
            with self.subTest(mode=expected), patch.object(reboot, "command", return_value=report):
                self.assertEqual(reboot.verify_post_reboot_state(), expected)
        with patch.object(reboot, "command", return_value="RESULT: INCOMPLETE"):
            with self.assertRaisesRegex(RuntimeError, "did not confirm a safe state"):
                reboot.verify_post_reboot_state()
        with patch.object(reboot, "command", side_effect=RuntimeError("bash exited")):
            with self.assertRaisesRegex(RuntimeError, "security report failed"):
                reboot.verify_post_reboot_state()

    def test_verifier_only_update_preserves_preflight_across_existing_reboot(self) -> None:
        state = {"version": 1, "boot_id": "boot-a", "revision": "x" * 40,
                 "preflight": "PASS"}

        def git_command(*args: str) -> str:
            if args[1] == "merge-base":
                return ""
            if args[1] == "diff":
                return "scripts/check_crawl4ai_vps_reboot.py\n"
            raise AssertionError(args)

        with patch.object(reboot, "command", side_effect=git_command):
            self.assertTrue(reboot.check_reboot(state, "boot-b", "y" * 40))
        with patch.object(reboot, "command", return_value="docker-compose.yml"):
            with self.assertRaisesRegex(RuntimeError, "Git revision changed"):
                reboot.check_reboot(state, "boot-b", "y" * 40)

    def test_same_boot_is_not_a_reboot(self) -> None:
        state = {"version": 1, "boot_id": "boot-a", "revision": "x" * 40,
                 "preflight": "PASS"}
        with self.assertRaisesRegex(RuntimeError, "has not rebooted"):
            reboot.check_reboot(state, "boot-a", "x" * 40)

    def test_changes_to_revision_or_incomplete_preflight_fail(self) -> None:
        state = {"version": 1, "boot_id": "boot-a", "revision": "x" * 40,
                 "preflight": "PASS"}
        with self.assertRaisesRegex(RuntimeError, "revision changed"):
            reboot.check_reboot(state, "boot-b", "y" * 40)
        state["preflight"] = "FAIL"
        with self.assertRaisesRegex(RuntimeError, "not successful"):
            reboot.check_reboot(state, "boot-b", "x" * 40)

    def test_private_report_survives_phase_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            state = {"version": 1, "boot_id": "boot-a", "revision": "x" * 40,
                     "preflight": "PASS"}
            reboot.save_state(path, state)
            self.assertEqual(reboot.load_state(path), state)
            self.assertEqual(json.loads(path.read_text()), state)

    def test_after_phase_checks_boot_before_running_live_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            reboot.save_state(path, {"version": 1, "boot_id": "boot-a",
                                     "revision": "x" * 40, "preflight": "PASS"})
            with (patch.object(reboot, "verify_host", return_value=("boot-a", "x" * 40)),
                  patch.object(reboot, "verify_post_reboot_state") as probe,
                  patch("sys.argv", ["check", "after", "--state-file", str(path)])):
                self.assertEqual(reboot.main(), 1)
                probe.assert_not_called()
                self.assertNotIn("postflight", reboot.load_state(path))

    def test_before_and_after_store_distinct_boot_ids_only_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            with (patch.object(reboot, "verify_host", return_value=("boot-a", "x" * 40)),
                  patch.object(reboot, "verify_firewall_and_live_egress") as probe,
                  patch("sys.argv", ["check", "before", "--state-file", str(path)])):
                self.assertEqual(reboot.main(), 0)
                probe.assert_called_once()
            state = reboot.load_state(path)
            self.assertEqual(state["boot_id"], "boot-a")
            self.assertNotIn("postflight", state)

            with (patch.object(reboot, "verify_host", return_value=("boot-b", "x" * 40)),
                  patch.object(reboot, "verify_post_reboot_state",
                               return_value="SAFE_DORMANT") as probe,
                  patch("sys.argv", ["check", "after", "--state-file", str(path)])):
                self.assertEqual(reboot.main(), 0)
                probe.assert_called_once()
            state = reboot.load_state(path)
            self.assertEqual(state["after_boot_id"], "boot-b")
            self.assertEqual(state["postflight"], "PASS")
            self.assertEqual(state["postflight_mode"], "SAFE_DORMANT")

    def test_after_records_both_revisions_if_only_verifier_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            reboot.save_state(path, {"version": 1, "boot_id": "boot-a",
                                     "revision": "x" * 40, "preflight": "PASS"})
            with (patch.object(reboot, "verify_host", return_value=("boot-b", "y" * 40)),
                  patch.object(reboot, "check_reboot", return_value=True),
                  patch.object(reboot, "verify_post_reboot_state", return_value="SAFE_DORMANT"),
                  patch("sys.argv", ["check", "after", "--state-file", str(path)])):
                self.assertEqual(reboot.main(), 0)
            state = reboot.load_state(path)
            self.assertEqual(state["preflight_revision"], "x" * 40)
            self.assertEqual(state["revision"], "y" * 40)
            self.assertEqual(state["postflight_mode"], "SAFE_DORMANT")


if __name__ == "__main__":
    unittest.main()
