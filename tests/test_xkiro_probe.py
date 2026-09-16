"""Offline contract tests for the xKiro live qualification probe."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import scripts.probe_xkiro as probe


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
