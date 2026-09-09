"""Regression coverage for tool-loop recovery across B.AI and Gemini."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

import httpx

from app.ai.bai import make_bai_provider
from app.ai.base import AllProvidersFailed, OpenAICompatProvider
from app.ai.gemini import make_gemini_provider
from app.ai.router import AIProviderRouter


class BaiNoToolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_plain_retry_explicitly_forces_tool_choice_none(self) -> None:
        requests: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
                request=request,
            )

        provider = _make_bai()
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://api.b.ai/v1/",
            transport=httpx.MockTransport(respond),
        )
        try:
            response = await provider.chat([{"role": "user", "content": "answer now"}], None)
        finally:
            await provider.aclose()

        self.assertEqual(response.content, "done")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get("tool_choice"), "none")
        self.assertNotIn("tools", requests[0])

    async def test_router_recovers_from_tool_budget_with_plain_bai_pass(self) -> None:
        requests: list[dict] = []
        executed: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            if len(requests) == 1:
                return _tool_response(request, "call_first")
            if len(requests) == 2:
                return _tool_response(request, "call_over_budget")

            history_has_result = any(
                message.get("role") == "tool" and message.get("content") == "fetched content"
                for message in payload["messages"]
            )
            if (
                payload.get("tool_choice") != "none"
                or "tools" in payload
                or not history_has_result
            ):
                return httpx.Response(
                    400,
                    json={"error": {"message": "plain recovery contract violated"}},
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "recovered from tool result"}}
                    ]
                },
                request=request,
            )

        provider = _make_bai()
        await provider.aclose()
        provider._client = httpx.AsyncClient(
            base_url="https://api.b.ai/v1/",
            transport=httpx.MockTransport(respond),
        )
        router = AIProviderRouter([provider], max_tool_rounds=1)

        async def execute(name: str, _args: dict) -> str:
            executed.append(name)
            return "fetched content"

        try:
            result = await router.complete(
                [{"role": "user", "content": "read this URL"}],
                [_fetch_url_tool()],
                execute,
            )
        finally:
            await provider.aclose()

        self.assertEqual(result, ("recovered from tool result", "bai"))
        self.assertEqual(executed, ["fetch_url"])
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[-1].get("tool_choice"), "none")
        self.assertNotIn("tools", requests[-1])

    async def test_local_tool_policy_failures_do_not_cool_down_bai_vision(self) -> None:
        bai_requests: list[dict] = []
        gemini_requests: list[dict] = []

        def bai_respond(request: httpx.Request) -> httpx.Response:
            bai_requests.append(json.loads(request.content))
            return _tool_response(request, f"call_{len(bai_requests)}")

        def gemini_respond(request: httpx.Request) -> httpx.Response:
            gemini_requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "fallback done"}}
                    ]
                },
                request=request,
            )

        bai = _make_bai_vision()
        await bai.aclose()
        bai._client = httpx.AsyncClient(
            base_url="https://bai.test/v1/",
            transport=httpx.MockTransport(bai_respond),
        )
        gemini = _make_gemini_vision()
        await gemini.aclose()
        gemini._client = httpx.AsyncClient(
            base_url="https://gemini.test/v1/",
            transport=httpx.MockTransport(gemini_respond),
        )
        router = AIProviderRouter([bai, gemini], max_tool_rounds=1)

        async def execute(_name: str, _args: dict) -> str:
            return "fetched content"

        try:
            result = await router.complete(
                [{"role": "user", "content": "inspect and research"}],
                [_fetch_url_tool()],
                execute,
                requires_vision=True,
                image_count=1,
            )
        finally:
            await bai.aclose()
            await gemini.aclose()

        self.assertEqual(result, ("fallback done", "gemini_vision"))
        self.assertEqual(len(bai_requests), 3)
        self.assertEqual(bai_requests[-1].get("tool_choice"), "none")
        self.assertEqual(len(gemini_requests), 1)
        self.assertTrue(bai.health.available())
        self.assertEqual(bai.health.consecutive_transient_failures, 0)


class GeminiCrossProviderFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_bai_tool_history_can_fallback_to_gemini_without_signature_400(self) -> None:
        bai_requests: list[dict] = []
        gemini_requests: list[dict] = []

        def bai_respond(request: httpx.Request) -> httpx.Response:
            bai_requests.append(json.loads(request.content))
            return _tool_response(request, f"call_{len(bai_requests)}")

        def gemini_respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            gemini_requests.append(payload)
            historical_calls = _assistant_tool_calls(payload)
            signature = _thought_signature(historical_calls[0]) if historical_calls else None
            if signature != "skip_thought_signature_validator":
                return _missing_signature_response(request)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "done"}}]},
                request=request,
            )

        bai = OpenAICompatProvider("bai", "https://bai.test/v1", "fake", "qwen3.8-flash")
        await bai.aclose()
        bai._client = httpx.AsyncClient(
            base_url="https://bai.test/v1/",
            transport=httpx.MockTransport(bai_respond),
        )

        gemini = _make_gemini()
        await gemini.aclose()
        gemini._client = httpx.AsyncClient(
            base_url="https://gemini.test/v1/",
            transport=httpx.MockTransport(gemini_respond),
        )
        router = AIProviderRouter([bai, gemini], max_tool_rounds=1)

        async def execute(_name: str, _args: dict) -> str:
            return "fetched content"

        try:
            try:
                result = await router.complete(
                    [{"role": "user", "content": "read this URL"}],
                    [_fetch_url_tool()],
                    execute,
                )
            except AllProvidersFailed as exc:
                self.fail(f"cross-provider fallback should succeed: {exc}")
        finally:
            await bai.aclose()
            await gemini.aclose()

        self.assertEqual(result, ("done", "gemini"))
        self.assertGreaterEqual(len(bai_requests), 2)
        self.assertEqual(len(gemini_requests), 1)
        assistant_calls = _assistant_tool_calls(gemini_requests[0])
        self.assertTrue(assistant_calls)
        self.assertEqual(
            _thought_signature(assistant_calls[0]),
            "skip_thought_signature_validator",
        )

    async def test_gemini_native_signature_survives_after_imported_bai_history(self) -> None:
        bai_requests: list[dict] = []
        gemini_requests: list[dict] = []
        native_signature = "gemini-native-signature"

        def bai_respond(request: httpx.Request) -> httpx.Response:
            bai_requests.append(json.loads(request.content))
            if len(bai_requests) == 1:
                return _tool_response(request, "call_bai")
            return httpx.Response(
                500,
                json={"error": {"message": "bai upstream failed"}},
                request=request,
            )

        def gemini_respond(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            gemini_requests.append(payload)
            calls = _assistant_tool_calls(payload)
            if len(gemini_requests) == 1:
                if not calls or _thought_signature(calls[0]) != (
                    "skip_thought_signature_validator"
                ):
                    return _missing_signature_response(request)
                return _tool_response(
                    request,
                    "call_gemini",
                    signature=native_signature,
                )

            signatures = {_thought_signature(call) for call in calls}
            if "skip_thought_signature_validator" not in signatures:
                return _missing_signature_response(request)
            if native_signature not in signatures:
                return httpx.Response(
                    400,
                    json={"error": {"message": "native Gemini signature was not replayed"}},
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "gemini continued"}}
                    ]
                },
                request=request,
            )

        bai = OpenAICompatProvider("bai", "https://bai.test/v1", "fake", "qwen3.8-flash")
        await bai.aclose()
        bai._client = httpx.AsyncClient(
            base_url="https://bai.test/v1/",
            transport=httpx.MockTransport(bai_respond),
        )

        gemini = _make_gemini()
        await gemini.aclose()
        gemini._client = httpx.AsyncClient(
            base_url="https://gemini.test/v1/",
            transport=httpx.MockTransport(gemini_respond),
        )
        router = AIProviderRouter([bai, gemini], max_tool_rounds=3)

        async def execute(_name: str, _args: dict) -> str:
            return "fetched content"

        try:
            result = await router.complete(
                [{"role": "user", "content": "read and continue"}],
                [_fetch_url_tool()],
                execute,
            )
        finally:
            await bai.aclose()
            await gemini.aclose()

        self.assertEqual(result, ("gemini continued", "gemini"))
        self.assertEqual(len(gemini_requests), 2)


def _bai_settings() -> SimpleNamespace:
    return SimpleNamespace(
        bai_api_key="secret",
        bai_text_model="qwen3.8-flash",
        bai_vision_model="qwen3.8-flash",
        bai_request_timeout_sec=30.0,
    )


def _make_bai():
    return make_bai_provider(_bai_settings())


def _make_bai_vision():
    return make_bai_provider(_bai_settings(), name="bai_vision", vision=True)


def _gemini_settings() -> SimpleNamespace:
    return SimpleNamespace(
        gemini_api_key="fake",
        gemini_model="gemini-3.8-flash",
        request_timeout_sec=30.0,
        max_images_per_request=3,
    )


def _make_gemini():
    return make_gemini_provider(_gemini_settings())


def _make_gemini_vision():
    return make_gemini_provider(
        _gemini_settings(),
        name="gemini_vision",
        model="gemini-3.8-flash",
        vision=True,
    )


def _tool_response(
    request: httpx.Request,
    call_id: str,
    *,
    signature: str | None = None,
) -> httpx.Response:
    tool_call = {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "fetch_url",
            "arguments": '{"url":"https://example.com"}',
        },
    }
    if signature is not None:
        tool_call["extra_content"] = {"google": {"thought_signature": signature}}
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [tool_call],
                    }
                }
            ]
        },
        request=request,
    )


def _assistant_tool_calls(payload: dict) -> list[dict]:
    return [
        call
        for message in payload["messages"]
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    ]


def _thought_signature(tool_call: dict) -> str | None:
    return (
        tool_call.get("extra_content", {})
        .get("google", {})
        .get("thought_signature")
    )


def _missing_signature_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        400,
        json={
            "error": {
                "code": 400,
                "message": (
                    "Function call is missing a thought_signature in functionCall parts."
                ),
                "status": "INVALID_ARGUMENT",
            }
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
