from __future__ import annotations

from pathlib import Path
import unittest

from scripts.probe_aurora import _bounded_redacted, _extract_chat_text, _extract_tool_call


class AuroraProbeHelperTests(unittest.TestCase):
    def test_output_is_bounded_and_secret_is_redacted(self) -> None:
        secret = "super-secret-key"
        result = _bounded_redacted(secret + ("x" * 1000), secret=secret, limit=120)
        self.assertNotIn(secret, result)
        self.assertLessEqual(len(result), 120)

    def test_chat_text_is_extracted(self) -> None:
        self.assertEqual(
            _extract_chat_text({"choices": [{"message": {"content": "ok"}}]}),
            "ok",
        )

    def test_tool_call_requires_expected_function(self) -> None:
        call = _extract_tool_call(
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

    def test_production_image_copies_scripts(self) -> None:
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY scripts ./scripts", dockerfile)


if __name__ == "__main__":
    unittest.main()
