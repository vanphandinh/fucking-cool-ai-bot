"""Offline tests for the manual B.AI contract probe."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
PROBE_PATH = ROOT / "scripts" / "probe_bai.py"


class BaiProbeTests(unittest.TestCase):
    def test_probe_script_exists(self) -> None:
        self.assertTrue(PROBE_PATH.is_file())

    def test_baseline_payload_never_adds_unverified_reasoning_fields(self) -> None:
        module = _load_probe()
        payload = module.build_chat_payload("qwen3.8-flash", include_tool=True)
        self.assertEqual(set(payload), {"model", "messages", "tools"})
        self.assertNotIn("enable_thinking", payload)
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("thinking", payload)

    def test_reasoning_overrides_are_explicitly_experimental(self) -> None:
        module = _load_probe()
        probes = module.experimental_reasoning_overrides()
        self.assertEqual(probes["qwen3.8-flash"], {"enable_thinking": False})
        self.assertEqual(probes["mimo-v2.5"], {"thinking": {"type": "disabled"}})
        self.assertEqual(probes["glm-5.3-flash"], {"reasoning_effort": "low"})
        self.assertNotIn("hy3", probes)


def _load_probe():
    spec = importlib.util.spec_from_file_location("probe_bai", PROBE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
