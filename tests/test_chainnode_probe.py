"""Offline tests for the retained Chainnode compatibility probe."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import unittest

import httpx


ROOT = Path(__file__).parents[1]
PROBE_PATH = ROOT / "scripts" / "probe_chainnode.py"
EXPECTED_MODELS = (
    "cl/z-ai/glm-5.3-flash",
    "cl/deepseek/deepseek-v4-flash",
    "cl/cline-free/muse-spark-1.3-contributor",
    "gweb/gemini-3.8-flash",
)


class ChainnodeProbeTests(unittest.TestCase):
    def test_probe_script_exists(self) -> None:
        self.assertTrue(PROBE_PATH.is_file())

    def test_default_model_matrix_contains_all_approved_candidates(self) -> None:
        module = _load_probe()
        self.assertEqual(module.DEFAULT_MODELS, EXPECTED_MODELS)

    def test_payloads_use_only_openai_compatible_fields(self) -> None:
        module = _load_probe()
        plain = module.build_chat_payload(EXPECTED_MODELS[0], mode="plain")
        stream = module.build_chat_payload(
            EXPECTED_MODELS[0],
            mode="plain",
            stream=True,
        )
        tool = module.build_chat_payload(EXPECTED_MODELS[0], mode="tool")

        self.assertEqual(set(plain), {"model", "messages"})
        self.assertEqual(set(stream), {"model", "messages", "stream"})
        self.assertIs(stream["stream"], True)
        self.assertEqual(set(tool), {"model", "messages", "tools"})
        self.assertEqual(tool["tools"][0]["function"]["name"], "echo_probe")
        for key in ("reasoning_effort", "thinking", "enable_thinking"):
            self.assertNotIn(key, plain)
            self.assertNotIn(key, tool)

    def test_inspector_rejects_wrapped_or_internal_markup(self) -> None:
        module = _load_probe()
        request = httpx.Request(
            "POST",
            "https://dn.chainno.de/v1/chat/completions",
        )
        wrapped = httpx.Response(
            200,
            json={
                "success": True,
                "data": {
                    "choices": [
                        {"message": {"role": "assistant", "content": "ok"}}
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
                            "content": (
                                "<｜DSML｜function_calls>bad"
                                "</｜DSML｜function_calls>"
                            ),
                        }
                    }
                ]
            },
            request=request,
        )

        wrapped_result = module.inspect_chat_response(
            wrapped,
            expect_tool=False,
        )
        markup_result = module.inspect_chat_response(
            markup,
            expect_tool=False,
        )
        self.assertEqual(
            wrapped_result["failure"],
            "missing_top_level_choices",
        )
        self.assertEqual(
            markup_result["failure"],
            "internal_tool_markup_leak",
        )

    def test_tool_continuation_replays_exact_call_id(self) -> None:
        module = _load_probe()
        initial = module.build_chat_payload(EXPECTED_MODELS[0], mode="tool")
        assistant_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_exact",
                    "type": "function",
                    "function": {
                        "name": "echo_probe",
                        "arguments": '{"value":"CHAINNODE_TOOL_PROBE"}',
                    },
                }
            ],
        }
        continuation = module.build_tool_continuation(
            initial,
            assistant_message,
        )
        self.assertEqual(continuation["messages"][-1]["role"], "tool")
        self.assertEqual(
            continuation["messages"][-1]["tool_call_id"],
            "call_exact",
        )
        self.assertEqual(continuation["tools"], initial["tools"])

    def test_post_chat_forces_explicit_nonstream(self) -> None:
        module = _load_probe()

        class Client:
            def __init__(self) -> None:
                self.payloads: list[dict] = []

            async def post(self, path: str, json: dict) -> httpx.Response:
                self.payloads.append(dict(json))
                request = httpx.Request(
                    "POST",
                    "https://dn.chainno.de/v1/" + path,
                )
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "ok",
                                }
                            }
                        ]
                    },
                    request=request,
                )

        client = Client()
        response, summary = asyncio.run(
            module.post_chat(
                client,
                {"model": EXPECTED_MODELS[0], "messages": []},
                max_retries=0,
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(summary["retry_count"], 0)
        self.assertIs(client.payloads[0]["stream"], False)


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "probe_chainnode",
        PROBE_PATH,
    )
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load Chainnode probe")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
