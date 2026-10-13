"""Offline checks for the actual VPS reboot verification gate."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_crawl4ai_vps_reboot as reboot


class RebootGateTests(unittest.TestCase):
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
                  patch.object(reboot, "verify_firewall_and_live_egress") as probe,
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
                  patch.object(reboot, "verify_firewall_and_live_egress") as probe,
                  patch("sys.argv", ["check", "after", "--state-file", str(path)])):
                self.assertEqual(reboot.main(), 0)
                probe.assert_called_once()
            state = reboot.load_state(path)
            self.assertEqual(state["after_boot_id"], "boot-b")
            self.assertEqual(state["postflight"], "PASS")


if __name__ == "__main__":
    unittest.main()
