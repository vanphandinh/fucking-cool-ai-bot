"""Regression coverage for learned tool capability across provider targets."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

import httpx

from app.ai.router import AIProviderRouter
from app.ai.xkiro import make_xkiro_provider


class ProviderToolCapabilityCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_tools_is_learned_across_credential_siblings(self) -> None:
        first_requests: list[dict] = []
        second_requests: list[dict] = []

        def first_respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            first_requests.append(payload)
            if "tools" in payload:
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "model does not support tool calling",
                        }
                    },
                    request=request,
                )
            return _text_response(request, "first plain")

        def second_respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            second_requests.append(payload)
            return _text_response(request, "second plain")

        first_provider = _make_xkiro_target(
            api_key="key-one",
            credential_id="cred-1",
            target_id="xkiro:text:m1:c1",
        )
        second_provider = _make_xkiro_target(
            api_key="key-two",
            credential_id="cred-2",
            target_id="xkiro:text:m1:c2",
        )
        await first_provider.aclose()
        await second_provider.aclose()
        first_provider._client = _mock_client(first_respond)
        second_provider._client = _mock_client(second_respond)
        router = AIProviderRouter(
            [first_provider, second_provider],
            text_provider_order=("xkiro",),
            vision_provider_order=(),
        )

        async def noop_tool(_name: str, _args: dict) -> str:
            return "unused"

        try:
            first_result = await router.complete(
                [{"role": "user", "content": "answer with tools if supported"}],
                [_fetch_url_tool()],
                noop_tool,
            )
            first_provider.health.record_disabled("force credential sibling")
            second_result = await router.complete(
                [{"role": "user", "content": "answer with tools if supported"}],
                [_fetch_url_tool()],
                noop_tool,
            )
        finally:
            await first_provider.aclose()
            await second_provider.aclose()

        self.assertEqual(first_result.content, "first plain")
        self.assertEqual(second_result.content, "second plain")
        self.assertEqual(len(first_requests), 2)
        self.assertIn("tools", first_requests[0])
        self.assertNotIn("tools", first_requests[1])
        self.assertEqual(len(second_requests), 1)
        self.assertNotIn(
            "tools",
            second_requests[0],
            "credential sibling must reuse the model-level learned no-tools capability",
        )


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        xkiro_api_key="unused",
        xkiro_text_model="shared-model",
        xkiro_vision_model="shared-model",
        xkiro_request_timeout_sec=30.0,
    )


def _make_xkiro_target(*, api_key: str, credential_id: str, target_id: str):
    return make_xkiro_provider(
        _settings(),
        api_key=api_key,
        model="shared-model",
        credential_id=credential_id,
        target_id=target_id,
    )


def _mock_client(responder) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.xkiro.com/v1/",
        transport=httpx.MockTransport(responder),
    )


def _text_response(request: httpx.Request, content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"role": "assistant", "content": content}},
            ]
        },
        request=request,
    )


def _fetch_url_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
