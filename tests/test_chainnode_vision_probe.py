"""Offline tests for the Chainnode vision qualification probe."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx


ROOT = Path(__file__).parents[1]
PROBE_PATH = ROOT / "scripts" / "probe_chainnode_vision.py"
EXPECTED_MODELS = (
    "cl/cline-free/muse-spark-1.3-contributor",
    "cl/z-ai/glm-5.3-flash",
    "cl/cline-free/deepseek-v4.1-flash",
)
EXPECTED_CODE = "47-GREEN-CIRCLE"


class ChainnodeVisionProbeTests(unittest.TestCase):
    def test_probe_script_exists(self) -> None:
        self.assertTrue(PROBE_PATH.is_file())

    def test_probe_uses_first_canonical_credential_pool_entry(self) -> None:
        module = _load_probe()
        with patch.dict(
            os.environ,
            {"AI_PROVIDERS__CHAINNODE__API_KEYS": "  VisionKeyOne  , VisionKeyTwo ,  "},
            clear=False,
        ):
            self.assertEqual(module.chainnode_probe_credential(), "VisionKeyOne")

    def test_default_model_matrix_matches_requested_candidates(self) -> None:
        module = _load_probe()
        self.assertEqual(module.DEFAULT_MODELS, EXPECTED_MODELS)

    def test_vision_payload_matches_runtime_image_url_shape(self) -> None:
        module = _load_probe()
        payload = module.build_vision_payload(EXPECTED_MODELS[0], mode="vision")

        self.assertEqual(set(payload), {"model", "messages"})
        self.assertEqual(payload["messages"][0]["role"], "system")
        user = payload["messages"][1]
        self.assertEqual(user["role"], "user")
        self.assertIsInstance(user["content"], list)
        self.assertEqual(
            [part["type"] for part in user["content"]],
            ["text", "image_url"],
        )
        self.assertNotIn(EXPECTED_CODE, user["content"][0]["text"])
        image_url = user["content"][1]["image_url"]["url"]
        self.assertTrue(image_url.startswith("data:image/png;base64,"))

    def test_stream_payload_sets_only_standard_stream_flag(self) -> None:
        module = _load_probe()
        payload = module.build_vision_payload(
            EXPECTED_MODELS[0],
            mode="vision",
            stream=True,
        )
        self.assertEqual(set(payload), {"model", "messages", "stream"})
        self.assertIs(payload["stream"], True)

    def test_tool_payload_keeps_image_and_openai_tool_schema(self) -> None:
        module = _load_probe()
        payload = module.build_vision_payload(EXPECTED_MODELS[0], mode="tool")
        user = payload["messages"][1]
        self.assertEqual(
            [part["type"] for part in user["content"]],
            ["text", "image_url"],
        )
        self.assertEqual(payload["tools"][0]["function"]["name"], "report_vision_probe")
        self.assertNotIn(EXPECTED_CODE, user["content"][0]["text"])

    def test_vision_inspector_requires_exact_hidden_image_code(self) -> None:
        module = _load_probe()
        request = httpx.Request("POST", "https://dn.chainno.de/v1/chat/completions")
        good = httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": EXPECTED_CODE}}
                ]
            },
            request=request,
        )
        bad = httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "47-GREEN-SQUARE"}}
                ]
            },
            request=request,
        )

        self.assertTrue(module.inspect_vision_response(good)["compatible"])
        result = module.inspect_vision_response(bad)
        self.assertFalse(result["compatible"])
        self.assertEqual(result["failure"], "vision_code_mismatch")

    def test_inspector_rejects_wrapped_and_internal_markup(self) -> None:
        module = _load_probe()
        request = httpx.Request("POST", "https://dn.chainno.de/v1/chat/completions")
        wrapped = httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "choices": [
                        {"message": {"role": "assistant", "content": EXPECTED_CODE}}
                    ]
                },
            },
            request=request,
        )
        markup = httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "<tool_call>leak</tool_call>",
                        }
                    }
                ]
            },
            request=request,
        )

        self.assertEqual(
            module.inspect_vision_response(wrapped)["failure"],
            "missing_top_level_choices",
        )
        self.assertEqual(
            module.inspect_vision_response(markup)["failure"],
            "internal_tool_markup_leak",
        )

    def test_tool_inspector_marks_explicit_tool_unsupported_http_error(self) -> None:
        module = _load_probe()
        request = httpx.Request("POST", "https://dn.chainno.de/v1/chat/completions")
        response = httpx.Response(
            400,
            json={"error": {"message": "This model does not support tools"}},
            request=request,
        )

        result = module.inspect_tool_response(response)
        self.assertFalse(result["compatible"])
        self.assertIs(result["supported"], False)
        self.assertEqual(result["failure"], "tools_unsupported")

    def test_tool_inspector_validates_image_derived_argument(self) -> None:
        module = _load_probe()
        request = httpx.Request("POST", "https://dn.chainno.de/v1/chat/completions")
        response = httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_vision",
                                    "type": "function",
                                    "function": {
                                        "name": "report_vision_probe",
                                        "arguments": json.dumps(
                                            {"code": EXPECTED_CODE}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
            request=request,
        )

        result = module.inspect_tool_response(response)
        self.assertTrue(result["compatible"])
        self.assertEqual(result["tool_call_id"], "call_vision")

    def test_tool_continuation_replays_exact_call_and_multimodal_message(self) -> None:
        module = _load_probe()
        initial = module.build_vision_payload(EXPECTED_MODELS[0], mode="tool")
        assistant = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_exact",
                    "type": "function",
                    "function": {
                        "name": "report_vision_probe",
                        "arguments": json.dumps({"code": EXPECTED_CODE}),
                    },
                }
            ],
        }
        continuation = module.build_tool_continuation(initial, assistant)

        self.assertEqual(continuation["messages"][1], initial["messages"][1])
        self.assertEqual(continuation["messages"][-1]["role"], "tool")
        self.assertEqual(continuation["messages"][-1]["tool_call_id"], "call_exact")
        self.assertEqual(continuation["tools"], initial["tools"])

    def test_stream_inspector_reassembles_exact_code(self) -> None:
        module = _load_probe()
        events = [
            {"choices": [{"delta": {"content": "47-GREEN-"}}]},
            {"choices": [{"delta": {"content": "CIRCLE"}}]},
        ]
        result = module.inspect_stream_events(events)
        self.assertTrue(result["compatible"])
        self.assertEqual(result["content"], EXPECTED_CODE)

    def test_post_chat_forces_explicit_nonstream(self) -> None:
        module = _load_probe()

        class Client:
            def __init__(self) -> None:
                self.payloads: list[dict] = []

            async def post(self, path: str, json: dict) -> httpx.Response:
                self.payloads.append(dict(json))
                request = httpx.Request("POST", "https://dn.chainno.de/v1/" + path)
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"role": "assistant", "content": EXPECTED_CODE}}
                        ]
                    },
                    request=request,
                )

        client = Client()
        response = asyncio.run(
            module.post_chat(
                client,
                module.build_vision_payload(EXPECTED_MODELS[0], mode="vision"),
                max_retries=0,
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertIs(client.payloads[0]["stream"], False)


def _load_probe():
    spec = importlib.util.spec_from_file_location("probe_chainnode_vision", PROBE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load Chainnode vision probe")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
