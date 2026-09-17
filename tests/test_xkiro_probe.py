"""Offline contract tests for the xKiro live qualification probe."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import scripts.probe_xkiro as probe


ROOT = Path(__file__).parents[1]
FREE_TEXT_MODEL = {
    "id": "free-text",
    "access_tier": "free",
    "pricing": {"input": 0, "output": 0},
    "capabilities": {"tools": True, "vision": False},
    "context_length": 131072,
}
FREE_VISION_MODEL = {
    "id": "free-vision",
    "access_tier": "free",
    "pricing": {"input": "0", "output": "0.0"},
    "capabilities": {"tools": True, "vision": True},
    "context_length": 262144,
}


def _structured_tool_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "echo_probe",
                                    "arguments": json.dumps(
                                        {"value": "XKIRO_TOOL_PROBE"}
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


class XKiroProbeCliTests(unittest.TestCase):
    def test_direct_script_invocation_imports_app_package(self) -> None:
        env = dict(os.environ)
        retired_probe_key = "XKIRO_PROBE_API_" + "KEY"
        env.pop(retired_probe_key, None)
        env["XKIRO_API_KEYS"] = ""
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "probe_xkiro.py"),
                "--text-model",
                "free-text",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('"error": "XKIRO_API_KEYS is required"', result.stdout)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_parse_args_rejects_invalid_numeric_bounds(self) -> None:
        invalid_argv = (
            ["--text-model", "free-text", "--timeout", "0"],
            ["--text-model", "free-text", "--max-retries", "-1"],
            ["--text-model", "free-text", "--retry-base-delay", "-0.1"],
            ["--text-model", "free-text", "--max-retry-after", "-0.1"],
        )
        for argv in invalid_argv:
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as exc:
                    probe.parse_args(argv)
                self.assertEqual(exc.exception.code, 2)


class XKiroCatalogTests(unittest.TestCase):
    def test_catalog_lookup_requires_exact_model_id(self) -> None:
        catalog = {"data": [FREE_TEXT_MODEL, FREE_VISION_MODEL]}
        self.assertEqual(probe.find_model(catalog, "free-text"), FREE_TEXT_MODEL)
        self.assertIsNone(probe.find_model(catalog, "FREE-TEXT"))
        self.assertIsNone(probe.find_model(catalog, "missing"))

    def test_free_gate_rejects_non_free_access_tier(self) -> None:
        model = {**FREE_TEXT_MODEL, "access_tier": "paid"}
        errors = probe.qualification_errors(model, require_vision=False)
        self.assertTrue(any("access_tier" in error for error in errors))

    def test_free_gate_rejects_nonzero_input_or_output_pricing(self) -> None:
        for pricing in (
            {"input": 0.01, "output": 0},
            {"input": 0, "output": "0.02"},
        ):
            with self.subTest(pricing=pricing):
                model = {**FREE_TEXT_MODEL, "pricing": pricing}
                errors = probe.qualification_errors(model, require_vision=False)
                self.assertTrue(any("pricing" in error for error in errors))

    def test_text_candidate_requires_tools(self) -> None:
        model = {
            **FREE_TEXT_MODEL,
            "capabilities": {"tools": False, "vision": False},
        }
        errors = probe.qualification_errors(model, require_vision=False)
        self.assertTrue(any("tools" in error for error in errors))

    def test_vision_candidate_requires_vision_and_tools(self) -> None:
        self.assertEqual(
            probe.qualification_errors(FREE_VISION_MODEL, require_vision=True),
            [],
        )
        model = {
            **FREE_VISION_MODEL,
            "capabilities": {"tools": True, "vision": False},
        }
        errors = probe.qualification_errors(model, require_vision=True)
        self.assertTrue(any("vision" in error for error in errors))


class XKiroProbeEnvironmentContractTests(unittest.IsolatedAsyncioTestCase):
    def test_probe_uses_first_canonical_credential_pool_entry(self) -> None:
        with patch.dict(
            os.environ,
            {"XKIRO_API_KEYS": "  ProbeOne , ProbeTwo ,  "},
            clear=True,
        ):
            self.assertEqual(probe.xkiro_probe_credential(), "ProbeOne")

    async def test_probe_accepts_canonical_credential_pool(self) -> None:
        args = probe.parse_args(["--text-model", "free-text"])
        with patch.dict(
            os.environ,
            {"XKIRO_API_KEYS": "probe-secret"},
            clear=True,
        ):
            with patch.object(
                probe,
                "fetch_catalog",
                side_effect=ValueError("stop-after-auth"),
            ):
                with patch.object(probe, "emit") as emit:
                    result = await probe.run_probe(args)

        self.assertEqual(result, 1)
        self.assertEqual(
            emit.call_args_list[-1].kwargs,
            {"compatible": False, "error": "stop-after-auth"},
        )

    async def test_all_catalog_gates_run_before_live_probe(self) -> None:
        args = probe.parse_args(
            [
                "--text-model",
                "free-text",
                "--vision-model",
                "missing-vision",
                "--image",
                "fixture.png",
            ]
        )
        catalog = {"data": [FREE_TEXT_MODEL]}
        with patch.dict(os.environ, {"XKIRO_API_KEYS": "probe-secret"}, clear=True):
            with patch.object(probe, "fetch_catalog", new=AsyncMock(return_value=catalog)):
                with patch.object(probe, "_probe_plain", new=AsyncMock()) as plain:
                    with patch.object(
                        probe,
                        "_probe_tool_flow",
                        new=AsyncMock(),
                    ) as tool_flow:
                        result = await probe.run_probe(args)

        self.assertEqual(result, 1)
        plain.assert_not_awaited()
        tool_flow.assert_not_awaited()

    async def test_emits_every_requested_catalog_gate_before_failing(self) -> None:
        paid_text_model = {
            "id": "paid-text",
            "access_tier": "paid",
            "pricing": {"input": 0, "output": 0},
            "capabilities": {"tools": True, "vision": False},
            "context_length": 131072,
        }
        args = probe.parse_args(
            [
                "--text-model",
                "paid-text",
                "--vision-model",
                "free-vision",
                "--image",
                "fixture.png",
            ]
        )
        catalog = {"data": [paid_text_model, FREE_VISION_MODEL]}

        async def forbidden_probe(*args: object, **kwargs: object) -> None:
            raise AssertionError("live probe must not run after any catalog gate failure")

        output = io.StringIO()
        with patch.dict(os.environ, {"XKIRO_API_KEYS": "probe-secret"}, clear=True):
            with patch.object(probe, "fetch_catalog", new=AsyncMock(return_value=catalog)):
                with patch.object(probe, "_probe_plain", new=forbidden_probe):
                    with patch.object(probe, "_probe_tool_flow", new=forbidden_probe):
                        with redirect_stdout(output):
                            result = await probe.run_probe(args)

        events = [json.loads(line) for line in output.getvalue().splitlines()]
        gates = [event for event in events if event["event"] == "catalog_gate"]
        self.assertEqual(result, 1)
        self.assertEqual(
            [(gate["route"], gate["model"], gate["compatible"]) for gate in gates],
            [
                ("text", "paid-text", False),
                ("vision", "free-vision", True),
            ],
        )


class XKiroPayloadTests(unittest.TestCase):
    def test_baseline_payload_is_explicitly_non_streaming(self) -> None:
        messages = [{"role": "user", "content": "hello"}]
        payload = probe.build_chat_payload("free-text", messages)
        self.assertEqual(set(payload), {"model", "messages", "stream"})
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["messages"], messages)

        with_tools = probe.build_chat_payload(
            "free-text",
            messages,
            tools=[probe.TOOL_SCHEMA],
        )
        self.assertEqual(set(with_tools), {"model", "messages", "tools", "stream"})
        self.assertNotIn("tool_choice", with_tools)

    def test_tool_probe_rejects_missing_structured_tool_call(self) -> None:
        response = {
            "choices": [
                {"message": {"role": "assistant", "content": "answered directly"}}
            ]
        }
        with self.assertRaisesRegex(ValueError, "structured tool call"):
            probe.extract_tool_call(response)

    def test_tool_continuation_replays_exact_assistant_message_and_tool_call_id(self) -> None:
        assistant = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_123",
                    "type": "function",
                    "function": {
                        "name": "echo_probe",
                        "arguments": json.dumps({"value": "XKIRO_TOOL_PROBE"}),
                    },
                }
            ],
        }
        response = {"choices": [{"message": assistant}]}
        tool_call = probe.extract_tool_call(response)
        base_messages = [
            {"role": "system", "content": "probe"},
            {"role": "user", "content": "call the tool"},
        ]
        payload = probe.build_tool_continuation(
            "free-text",
            base_messages,
            assistant,
            tool_call,
        )
        self.assertIs(payload["messages"][-2], assistant)
        self.assertEqual(
            payload["messages"][-1],
            {
                "role": "tool",
                "tool_call_id": "call_123",
                "content": "XKIRO_TOOL_PROBE_OK",
            },
        )
        self.assertEqual(payload["tools"], [probe.TOOL_SCHEMA])
        self.assertIs(payload["stream"], False)

    def test_vision_message_uses_openai_image_url_data_url_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.png"
            path.write_bytes(b"not-a-real-png-but-sufficient-for-encoding")
            data_url = probe.image_data_url(path)
        message = probe.vision_message(data_url)
        self.assertEqual(message["role"], "user")
        self.assertEqual(message["content"][1]["type"], "image_url")
        self.assertTrue(
            message["content"][1]["image_url"]["url"].startswith(
                "data:image/png;base64,"
            )
        )


class XKiroToolContinuationTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_flow_rejects_nonsemantic_continuation(self) -> None:
        requests = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if requests == 1:
                return _structured_tool_response(request)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "Hello"}}
                    ]
                },
                request=request,
            )

        async with httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(respond),
        ) as client:
            with patch("scripts.probe_xkiro.secrets", create=True) as secrets_module:
                secrets_module.token_hex.return_value = "abc123"
                with self.assertRaisesRegex(ValueError, "tool result marker"):
                    await probe._probe_tool_flow(
                        client,
                        "free-text",
                        max_retries=0,
                        retry_base_delay=0,
                        max_retry_after=probe.DEFAULT_MAX_RETRY_AFTER,
                    )

    async def test_tool_flow_uses_unique_marker_and_accepts_exact_echo(self) -> None:
        request_payloads: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            request_payloads.append(json.loads(request.content))
            if len(request_payloads) == 1:
                return _structured_tool_response(request)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "XKIRO_TOOL_RESULT_abc123",
                            }
                        }
                    ]
                },
                request=request,
            )

        async with httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(respond),
        ) as client:
            with patch("scripts.probe_xkiro.secrets", create=True) as secrets_module:
                secrets_module.token_hex.return_value = "abc123"
                await probe._probe_tool_flow(
                    client,
                    "free-text",
                    max_retries=0,
                    retry_base_delay=0,
                    max_retry_after=probe.DEFAULT_MAX_RETRY_AFTER,
                )

        tool_message = request_payloads[1]["messages"][-1]
        self.assertIn("XKIRO_TOOL_RESULT_abc123", tool_message["content"])
        self.assertNotEqual(tool_message["content"], probe.TOOL_RESULT)


class XKiroRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_chat_honors_retry_after_for_429(self) -> None:
        requests = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            if requests == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "1.25"},
                    json={"error": {"message": "rate limited"}},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                request=request,
            )

        async with httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(respond),
        ) as client:
            with patch("scripts.probe_xkiro.asyncio.sleep", new=AsyncMock()) as sleep:
                response, summary = await probe.post_chat(
                    client,
                    probe.build_chat_payload(
                        "free-text",
                        [{"role": "user", "content": "hello"}],
                    ),
                    max_retries=1,
                    retry_base_delay=0.01,
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(summary["retry_count"], 1)
        sleep.assert_awaited_once_with(1.25)

    async def test_post_chat_rejects_retry_after_above_wait_budget(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"Retry-After": "3600"},
                json={"error": {"message": "rate limited"}},
                request=request,
            )

        async with httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(respond),
        ) as client:
            with patch("scripts.probe_xkiro.asyncio.sleep", new=AsyncMock()) as sleep:
                with self.assertRaisesRegex(ValueError, "Retry-After"):
                    await probe.post_chat(
                        client,
                        probe.build_chat_payload(
                            "free-text",
                            [{"role": "user", "content": "hello"}],
                        ),
                        max_retries=1,
                        retry_base_delay=0.01,
                        max_retry_after=60.0,
                    )

        sleep.assert_not_awaited()

    async def test_post_chat_stops_after_retry_limit(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={"error": {"message": "rate limited"}},
                request=request,
            )

        async with httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(respond),
        ) as client:
            with patch("scripts.probe_xkiro.asyncio.sleep", new=AsyncMock()):
                response, summary = await probe.post_chat(
                    client,
                    probe.build_chat_payload(
                        "free-text",
                        [{"role": "user", "content": "hello"}],
                    ),
                    max_retries=1,
                    retry_base_delay=0,
                )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(summary["retry_count"], 1)


if __name__ == "__main__":
    unittest.main()
