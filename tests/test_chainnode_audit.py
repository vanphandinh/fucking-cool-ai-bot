"""Security and runtime-safety regressions for the Chainnode integration."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

import httpx

from app.ai.base import ProviderError
from app.ai.contracts import ChatMessage, ChatRequest, ImagePart, TextPart, ToolDefinition
from app.ai.router import build_provider_router
from app.ai.target_builder import build_provider_targets
from app.config import Settings
from tests.provider_fakes import text_request


ROOT = Path(__file__).parents[1]
PROBE_PATH = ROOT / "scripts" / "probe_chainnode.py"
CHAINNODE_FILES = (
    ROOT / "scripts" / "probe_chainnode.py",
    ROOT / "scripts" / "probe_chainnode_vision.py",
    ROOT / ".env.example",
)
QUALIFIED_MODEL = "cl/cline-free/deepseek-v4.1-flash"
VISION_MODEL = "cl/cline-free/muse-spark-1.3-contributor"


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
                    text_request("latest?", tools=(_search_tool(),))
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
                            text_request("latest?", tools=(_search_tool(),))
                        )
                finally:
                    await provider.aclose()

    async def test_dsml_markup_is_rejected_with_structured_tool_calls(self) -> None:
        provider = _provider()
        await provider.aclose()

        def respond(request: httpx.Request) -> httpx.Response:
            content = "<｜DSML｜function_calls>internal</｜DSML｜function_calls>"
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
                    text_request("latest?", tools=(_search_tool(),))
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
                        await provider.chat(text_request("hi"))
                finally:
                    await provider.aclose()
                self.assertEqual(ctx.exception.status_code, status)
                self.assertEqual(ctx.exception.transient, transient)
                self.assertEqual(ctx.exception.retry_after, retry_after)

    async def test_chainnode_503_falls_back_to_xkiro_without_shared_health(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                ai_providers={
                    "chainnode":{"api_keys":"test-chainnode-key","text_models":QUALIFIED_MODEL},
                    "xkiro":{"api_keys":"test-xkiro-key","text_models":"test-xkiro-text"},
                },
                text_provider_order="chainnode,xkiro",
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

        def xkiro_success(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "ok"}}
                    ]
                },
                request=request,
            )

        providers["chainnode"]._client = httpx.AsyncClient(
            base_url="https://dn.chainno.de/v1/",
            transport=httpx.MockTransport(chainnode_failure),
        )
        providers["xkiro"]._client = httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(xkiro_success),
        )
        try:
            result = await router.complete(text_request("hi"), _noop_tool)
        finally:
            for provider in providers.values():
                await provider.aclose()

        self.assertEqual(result.provider, "xkiro")
        self.assertEqual(result.content, "ok")
        self.assertEqual(result.fallbacks, ("xkiro",))
        self.assertEqual(
            providers["chainnode"].health.consecutive_transient_failures,
            1,
        )
        self.assertEqual(providers["xkiro"].health.consecutive_transient_failures, 0)

    async def test_chainnode_vision_503_falls_back_to_xkiro_vision(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                ai_providers={
                    "chainnode":{"api_keys":"test-chainnode-key","vision_models":VISION_MODEL},
                    "xkiro":{"api_keys":"test-xkiro-key","text_models":"test-xkiro-text","vision_models":"test-xkiro-vision"},
                },
                text_provider_order="xkiro",
                vision_provider_order="chainnode,xkiro",
            )
        )
        providers = {
            (provider.name, provider.capabilities.route): provider
            for provider in router.providers
        }
        for provider in providers.values():
            await provider.aclose()

        def chainnode_failure(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                503,
                json={"error": {"message": "temporary vision outage"}},
                request=request,
            )

        def xkiro_success(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "vision fallback ok",
                            }
                        }
                    ]
                },
                request=request,
            )

        providers[("chainnode", "vision")]._client = httpx.AsyncClient(
            base_url="https://dn.chainno.de/v1/",
            transport=httpx.MockTransport(chainnode_failure),
        )
        providers[("xkiro", "vision")]._client = httpx.AsyncClient(
            base_url="https://api.xkiro.com/v1/",
            transport=httpx.MockTransport(xkiro_success),
        )
        request = ChatRequest(
            messages=(
                ChatMessage(
                    "user",
                    (
                        TextPart("Describe this image"),
                        ImagePart("image/png", b"hello"),
                    ),
                ),
            )
        )
        try:
            result = await router.complete(
                request,
                _noop_tool,
                requires_vision=True,
                image_count=1,
            )
        finally:
            for provider in providers.values():
                await provider.aclose()

        self.assertEqual(result.provider, "xkiro")
        self.assertEqual(result.content, "vision fallback ok")
        self.assertEqual(result.fallbacks, ("xkiro",))


def _provider():
    settings = Settings(
        _env_file=None,
        ai_providers={"chainnode":{"api_keys":"test-key","text_models":QUALIFIED_MODEL,"request_timeout_sec":30}},
        text_provider_order="chainnode",
        vision_enabled=False,
    )
    return build_provider_targets(settings, ["chainnode"])[0]


def _search_tool() -> ToolDefinition:
    return ToolDefinition(
        name="web_search",
        description="Search the web",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


async def _noop_tool(name: str, args: dict) -> str:
    return "unused"


if __name__ == "__main__":
    unittest.main()
