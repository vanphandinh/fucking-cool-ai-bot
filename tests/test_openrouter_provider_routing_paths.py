"""Regression coverage for provider routing on every OpenRouter chat path."""

from __future__ import annotations

import json
import unittest

import httpx

from app.ai.contracts import ChatMessage, ChatRequest, ImagePart, TextPart
from app.ai.router import AIProviderRouter
from app.ai.target_builder import build_provider_targets
from app.config import Settings
from tests.openai_target_fakes import make_catalog_target
from tests.provider_fakes import fetch_url_definition, noop_tool

_EXPECTED_PROVIDER = {
    "only": ["deepinfra", "together"],
    "order": ["deepinfra", "together"],
}
_EVIDENCE_MARKER = "FRESH-SYNTHESIS-EVIDENCE"


def _settings(*, provider: str = "deepinfra, together, deepinfra") -> Settings:
    return Settings(
        _env_file=None,
        ai_providers={
            "openrouter": {
                "api_keys": "test-key",
                "text_models": "text-model",
                "vision_models": "vision-model",
                "provider": provider,
            }
        },
        text_provider_order="openrouter",
        vision_provider_order="openrouter",
        vision_enabled=True,
    )


async def _install_transport(target, responder) -> None:
    await target.aclose()
    target._client = httpx.AsyncClient(
        base_url=str(target.spec.base_url).rstrip("/") + "/",
        transport=httpx.MockTransport(responder),
    )


def _text_response(request: httpx.Request, content: str = "ok") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": content}}]
        },
        request=request,
    )


def _tool_response(request: httpx.Request, call_id: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": "fetch_url",
                                    "arguments": json.dumps(
                                        {"url": "https://example.com/evidence"}
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


def _has_tool_history(payload: dict) -> bool:
    return any(
        message.get("role") == "tool" or message.get("tool_calls")
        for message in payload["messages"]
    )


class OpenRouterProviderRoutingPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_builder_attaches_preferences_to_text_and_vision_targets(self) -> None:
        targets = build_provider_targets(_settings(), ["openrouter"])
        try:
            self.assertEqual(
                [target.capabilities.route for target in targets],
                ["text", "vision"],
            )
            self.assertTrue(
                all(
                    target.spec.provider_preferences == ("deepinfra", "together")
                    for target in targets
                )
            )
        finally:
            for target in targets:
                await target.aclose()

    async def test_plain_text_chat_carries_provider(self) -> None:
        targets = build_provider_targets(_settings(), ["openrouter"])
        text = next(
            target for target in targets if target.capabilities.route == "text"
        )
        captured: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return _text_response(request)

        await _install_transport(text, respond)
        try:
            await text.chat(
                ChatRequest(messages=(ChatMessage("user", (TextPart("hello"),)),))
            )
        finally:
            for target in targets:
                await target.aclose()

        self.assertEqual(captured[0]["provider"], _EXPECTED_PROVIDER)

    async def test_vision_chat_carries_provider_with_image_content(self) -> None:
        targets = build_provider_targets(_settings(), ["openrouter"])
        vision = next(
            target for target in targets if target.capabilities.route == "vision"
        )
        captured: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return _text_response(request, "seen")

        await _install_transport(vision, respond)
        router = AIProviderRouter([vision], vision_provider_order=("openrouter",))
        try:
            result = await router.complete(
                ChatRequest(
                    messages=(
                        ChatMessage(
                            "user",
                            (
                                TextPart("look"),
                                ImagePart("image/png", b"\x89PNG"),
                            ),
                        ),
                    )
                ),
                noop_tool,
                requires_vision=True,
                image_count=1,
            )
        finally:
            for target in targets:
                await target.aclose()

        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(captured[0]["provider"], _EXPECTED_PROVIDER)
        content = captured[0]["messages"][0]["content"]
        self.assertEqual(
            [part["type"] for part in content],
            ["text", "image_url"],
        )

    async def test_tool_rounds_carry_provider_on_every_turn(self) -> None:
        targets = build_provider_targets(_settings(), ["openrouter"])
        text = next(
            target for target in targets if target.capabilities.route == "text"
        )
        captured: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            if len(captured) <= 2:
                return _tool_response(request, f"call_{len(captured)}")
            return _text_response(request, "done")

        await _install_transport(text, respond)
        router = AIProviderRouter([text], max_tool_rounds=3)

        async def execute(_name: str, _args: dict) -> str:
            return "tool evidence"

        try:
            result = await router.complete(
                ChatRequest(
                    messages=(ChatMessage("user", (TextPart("research"),)),),
                    tools=(fetch_url_definition(),),
                ),
                execute,
            )
        finally:
            for target in targets:
                await target.aclose()

        self.assertEqual(result.content, "done")
        self.assertEqual(len(captured), 3)
        self.assertTrue(
            all(
                payload["provider"] == _EXPECTED_PROVIDER
                for payload in captured
            )
        )
        self.assertTrue(all("tools" in payload for payload in captured))
        self.assertTrue(
            any(
                message.get("role") == "tool"
                for message in captured[1]["messages"]
            )
        )

    async def test_fresh_synthesis_after_history_compaction_carries_provider(self) -> None:
        targets = build_provider_targets(_settings(), ["openrouter"])
        text = next(
            target for target in targets if target.capabilities.route == "text"
        )
        captured: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            if len(captured) == 1:
                return _tool_response(request, "call_1")
            return _text_response(request, "synthesized")

        await _install_transport(text, respond)
        router = AIProviderRouter([text], max_tool_rounds=1)

        async def execute(_name: str, _args: dict) -> str:
            return _EVIDENCE_MARKER

        try:
            result = await router.complete(
                ChatRequest(
                    messages=(ChatMessage("user", (TextPart("research"),)),),
                    tools=(fetch_url_definition(),),
                ),
                execute,
            )
        finally:
            for target in targets:
                await target.aclose()

        self.assertEqual(result.content, "synthesized")
        self.assertEqual(len(captured), 2)
        synthesis = captured[1]
        self.assertEqual(synthesis["provider"], _EXPECTED_PROVIDER)
        self.assertNotIn("tools", synthesis)
        self.assertNotIn("tool_choice", synthesis)
        self.assertFalse(_has_tool_history(synthesis))
        self.assertIn(
            _EVIDENCE_MARKER,
            json.dumps(synthesis["messages"], ensure_ascii=False),
        )

    async def test_empty_provider_list_omits_provider_field(self) -> None:
        targets = build_provider_targets(_settings(provider=""), ["openrouter"])
        text = next(
            target for target in targets if target.capabilities.route == "text"
        )
        captured: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return _text_response(request)

        await _install_transport(text, respond)
        try:
            self.assertEqual(text.spec.provider_preferences, ())
            await text.chat(
                ChatRequest(messages=(ChatMessage("user", (TextPart("hello"),)),))
            )
        finally:
            for target in targets:
                await target.aclose()

        self.assertNotIn("provider", captured[0])

    async def test_non_opted_in_target_omits_provider_field(self) -> None:
        target = make_catalog_target("chainnode")
        captured: list[dict] = []

        def respond(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return _text_response(request)

        await _install_transport(target, respond)
        try:
            self.assertEqual(target.spec.provider_preferences, ())
            await target.chat(
                ChatRequest(messages=(ChatMessage("user", (TextPart("hello"),)),))
            )
        finally:
            await target.aclose()

        self.assertNotIn("provider", captured[0])


if __name__ == "__main__":
    unittest.main()
