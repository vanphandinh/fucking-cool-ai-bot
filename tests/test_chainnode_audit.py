"""Security and runtime-safety regressions for the Chainnode integration."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

import httpx

import app.ai.chainnode as chainnode
from app.ai.base import ProviderError
from app.ai.router import build_provider_router
from app.config import Settings


ROOT = Path(__file__).parents[1]
PROBE_PATH = ROOT / "scripts" / "probe_chainnode.py"
CHAINNODE_FILES = (
    ROOT / "app" / "ai" / "chainnode.py",
    ROOT / "scripts" / "probe_chainnode.py",
    ROOT / ".env.example",
)
QUALIFIED_MODEL = "cl/deepseek/deepseek-v4-flash"


class ChainnodeCliAuditTests(unittest.TestCase):
    def test_probe_is_directly_executable_from_repo_root(self) -> None:
        result = subprocess.run(
            [sys.executable, str(PROBE_PATH), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--models", result.stdout)
        self.assertNotIn("--api-key", result.stdout.lower())

    def test_chainnode_sources_contain_no_embedded_secret_shape(self) -> None:
        for path in CHAINNODE_FILES:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("sk-", text.lower(), str(path))


class ChainnodeRuntimeSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_structured_tool_arguments_fail_closed(self) -> None:
        provider = _provider()
        await provider.aclose()

        def respond(request: httpx.Request) -> httpx.Response:
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
                                        "id": "call_bad",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": "{not-json",
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                request=request,
            )

        provider._client = httpx.AsyncClient(
            base_url="https://dn.chainno.de/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            with self.assertRaisesRegex(ProviderError, "arguments"):
                await provider.chat(
                    [{"role": "user", "content": "latest?"}],
                    [_search_tool()],
                )
        finally:
            await provider.aclose()

    async def test_invalid_structured_tool_calls_fail_closed(self) -> None:
        cases = (
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": '{"query":"latest"}',
                },
            },
            {
                "id": "call_wrong_type",
                "type": "custom",
                "function": {
                    "name": "web_search",
                    "arguments": '{"query":"latest"}',
                },
            },
            {
                "id": "call_empty_name",
                "type": "function",
                "function": {
                    "name": "",
                    "arguments": '{"query":"latest"}',
                },
            },
            {
                "id": "call_unknown",
                "type": "function",
                "function": {
                    "name": "shell_exec",
                    "arguments": '{"query":"latest"}',
                },
            },
            "broken",
        )

        for tool_call in cases:
            with self.subTest(tool_call=tool_call):
                provider = _provider()
                await provider.aclose()

                def respond(
                    request: httpx.Request,
                    tool_call=tool_call,
                ) -> httpx.Response:
                    return httpx.Response(
                        200,
                        json={
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": None,
                                        "tool_calls": [tool_call],
                                    }
                                }
                            ]
                        },
                        request=request,
                    )

                provider._client = httpx.AsyncClient(
                    base_url="https://dn.chainno.de/v1/",
                    transport=httpx.MockTransport(respond),
                )
                try:
                    with self.assertRaises(ProviderError):
                        await provider.chat(
                            [{"role": "user", "content": "latest?"}],
                            [_search_tool()],
                        )
                finally:
                    await provider.aclose()

    async def test_dsml_markup_is_rejected_with_structured_tool_calls(self) -> None:
        provider = _provider()
        await provider.aclose()

        def respond(request: httpx.Request) -> httpx.Response:
            content = (
                "<｜DSML｜function_calls>internal"
                "</｜DSML｜function_calls>"
            )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": content,
                                "tool_calls": [
                                    {
                                        "id": "call_search",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query":"latest"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                request=request,
            )

        provider._client = httpx.AsyncClient(
            base_url="https://dn.chainno.de/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            with self.assertRaisesRegex(ProviderError, "tool"):
                await provider.chat(
                    [{"role": "user", "content": "latest?"}],
                    [_search_tool()],
                )
        finally:
            await provider.aclose()

    async def test_status_classification_matches_router_health_policy(self) -> None:
        cases = (
            (401, False, None),
            (403, False, None),
            (429, True, 7.0),
            (503, True, None),
        )
        for status, transient, retry_after in cases:
            with self.subTest(status=status):
                provider = _provider()
                await provider.aclose()

                def respond(
                    request: httpx.Request,
                    status=status,
                ) -> httpx.Response:
                    headers = {"Retry-After": "7"} if status == 429 else {}
                    return httpx.Response(
                        status,
                        headers=headers,
                        json={"error": {"message": "upstream failure"}},
                        request=request,
                    )

                provider._client = httpx.AsyncClient(
                    base_url="https://dn.chainno.de/v1/",
                    transport=httpx.MockTransport(respond),
                )
                try:
                    with self.assertRaises(ProviderError) as ctx:
                        await provider.chat(
                            [{"role": "user", "content": "hi"}]
                        )
                finally:
                    await provider.aclose()
                self.assertEqual(ctx.exception.status_code, status)
                self.assertEqual(ctx.exception.transient, transient)
                self.assertEqual(ctx.exception.retry_after, retry_after)

    async def test_chainnode_503_falls_back_to_bai_without_shared_health(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                chainnode_api_key="chainnode-secret",
                chainnode_text_model=QUALIFIED_MODEL,
                bai_api_key="bai-secret",
                text_provider_order="chainnode,bai",
                vision_enabled=False,
            )
        )
        providers = {provider.name: provider for provider in router.providers}
        for provider in providers.values():
            await provider.aclose()

        def chainnode_failure(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                503,
                json={"error": {"message": "temporary outage"}},
                request=request,
            )

        def bai_success(request: httpx.Request) -> httpx.Response:
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

        providers["chainnode"]._client = httpx.AsyncClient(
            base_url="https://dn.chainno.de/v1/",
            transport=httpx.MockTransport(chainnode_failure),
        )
        providers["bai"]._client = httpx.AsyncClient(
            base_url="https://api.b.ai/v1/",
            transport=httpx.MockTransport(bai_success),
        )
        try:
            result = await router.complete(
                [{"role": "user", "content": "hi"}],
                None,
                _noop_tool,
            )
        finally:
            for provider in providers.values():
                await provider.aclose()

        self.assertEqual(result.provider, "bai")
        self.assertEqual(result.content, "ok")
        self.assertEqual(result.fallbacks, ("bai",))
        self.assertEqual(
            providers["chainnode"].health.consecutive_transient_failures,
            1,
        )
        self.assertEqual(
            providers["bai"].health.consecutive_transient_failures,
            0,
        )


def _provider():
    return chainnode.make_chainnode_provider(
        SimpleNamespace(
            chainnode_api_key="secret",
            chainnode_base_url="https://dn.chainno.de/v1",
            chainnode_text_model=QUALIFIED_MODEL,
            chainnode_request_timeout_sec=30.0,
        )
    )


def _search_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }


async def _noop_tool(name: str, args: dict) -> str:
    return "unused"


if __name__ == "__main__":
    unittest.main()
