from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


def _load_probe_module():
    path = Path("scripts/probe_aurora.py")
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("probe_aurora_test_module", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AuroraProbeTests(unittest.TestCase):
    def test_probe_script_exists_and_is_packaged(self) -> None:
        self.assertTrue(Path("scripts/probe_aurora.py").is_file())
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY scripts ./scripts", dockerfile)

    def test_output_is_bounded_and_secret_is_redacted(self) -> None:
        module = _load_probe_module()
        self.assertIsNotNone(module, "Aurora probe script is missing")
        secret = "super-secret-key"
        result = module._bounded_redacted(secret + ("x" * 1000), secret=secret, limit=120)
        self.assertNotIn(secret, result)
        self.assertLessEqual(len(result), 120)

    def test_chat_and_tool_payload_helpers_accept_expected_shape(self) -> None:
        module = _load_probe_module()
        self.assertIsNotNone(module, "Aurora probe script is missing")
        self.assertEqual(
            module._extract_chat_text({"choices": [{"message": {"content": "ok"}}]}),
            "ok",
        )
        call = module._extract_tool_call(
            {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "echo_probe",
                                        "arguments": '{"value":"aurora"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
        )
        self.assertEqual(call["function"]["name"], "echo_probe")


if __name__ == "__main__":
    unittest.main()
