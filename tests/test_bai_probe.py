"""Offline tests for the manual B.AI contract probe."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx


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

    def test_tool_payload_explicitly_requests_echo_probe(self) -> None:
        module = _load_probe()
        payload = module.build_chat_payload("qwen3.8-flash", include_tool=True)
        self.assertIn("echo_probe", payload["messages"][-1]["content"])

    def test_response_summary_includes_content_preview(self) -> None:
        module = _load_probe()
        request = httpx.Request("POST", "https://api.b.ai/v1/chat/completions")
        response = httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "red on the left, blue on the right",
                        },
                    }
                ]
            },
            request=request,
        )
        summary = module._response_summary(response, 0.1)
        self.assertEqual(
            summary["content_preview"],
            "red on the left, blue on the right",
        )

    def test_post_chat_retries_429_and_honors_retry_after(self) -> None:
        module = _load_probe()

        class RateLimitedClient:
            def __init__(self) -> None:
                self.calls = 0

            async def post(self, path: str, json: dict) -> httpx.Response:
                self.calls += 1
                request = httpx.Request("POST", "https://api.b.ai/v1/" + path)
                if self.calls == 1:
                    return httpx.Response(
                        429,
                        headers={"Retry-After": "0"},
                        json={"error": {"message": "rate limited"}},
                        request=request,
                    )
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"role": "assistant", "content": "ok"}}
                        ]
                    },
                    request=request,
                )

        client = RateLimitedClient()
        response, summary = asyncio.run(
            module._post_chat(
                client,
                {"model": "qwen3.8-flash", "messages": []},
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.calls, 2)
        self.assertEqual(summary["retry_count"], 1)

    def test_post_chat_stops_after_configured_retry_limit(self) -> None:
        module = _load_probe()

        class AlwaysRateLimitedClient:
            def __init__(self) -> None:
                self.calls = 0

            async def post(self, path: str, json: dict) -> httpx.Response:
                self.calls += 1
                request = httpx.Request("POST", "https://api.b.ai/v1/" + path)
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0"},
                    json={"error": {"message": "rate limited"}},
                    request=request,
                )

        client = AlwaysRateLimitedClient()
        response, summary = asyncio.run(
            module._post_chat(
                client,
                {"model": "qwen3.8-flash", "messages": []},
                max_retries=2,
            )
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(client.calls, 3)
        self.assertEqual(summary["retry_count"], 2)

    def test_retry_delay_uses_exponential_backoff_with_jitter_without_header(self) -> None:
        module = _load_probe()
        request = httpx.Request("POST", "https://api.b.ai/v1/chat/completions")
        response = httpx.Response(429, request=request)
        with patch.object(module.random, "uniform", return_value=0.25):
            delay = module._retry_delay_seconds(response, retry_index=1, base_delay=1.0)
        self.assertEqual(delay, 2.25)

    def test_tool_probe_fails_when_model_does_not_call_tool(self) -> None:
        module = _load_probe()

        class TextOnlyClient:
            async def post(self, _path: str, json: dict) -> httpx.Response:
                request = httpx.Request("POST", "https://api.b.ai/v1/chat/completions", json=json)
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"role": "assistant", "content": "plain text"}}
                        ]
                    },
                    request=request,
                )

        records, ok = asyncio.run(
            module._probe_one(
                TextOnlyClient(),
                "qwen3.8-flash",
                tools=True,
                image_data_url=None,
                experimental_reasoning=False,
            )
        )
        self.assertFalse(ok)
        self.assertTrue(any(record.get("probe") == "tool_call_missing" for record in records))

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
