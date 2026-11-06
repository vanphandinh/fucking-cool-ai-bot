from __future__ import annotations

import base64
import unittest
from types import MappingProxyType

import httpx

from app.ai.base import ProviderError
from app.ai.capabilities import ProviderCapabilities
from app.ai.catalog import load_provider_catalog
from app.ai.contracts import (
    ChatMessage,
    ChatRequest,
    ImagePart,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
)
from app.ai.drivers.openai_chat import (
    OpenAIChatDriver,
    parse_response,
    serialize_request,
)
from app.ai.recovery import RecoveryPolicy
from app.ai.session import new_provider_session_id, provider_session_id
from app.ai.target import ProviderTargetIdentity, TargetSpec


TOOL = ToolDefinition(
    name="fetch_url",
    description="Fetch URL",
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
)


class OpenAIChatDriverTests(unittest.TestCase):
    def test_serializer_handles_text_image_tools_and_provider_state(self) -> None:
        raw = b"\x89PNG\x00raw"
        request = ChatRequest(
            messages=(
                ChatMessage("system", (TextPart("system"),)),
                ChatMessage(
                    "user",
                    (TextPart("look"), ImagePart("image/png", raw)),
                ),
                ChatMessage(
                    "assistant",
                    (
                        TextPart(""),
                        ToolCallPart("c1", "fetch_url", {"url": "https://example.com"}),
                    ),
                    provider_state={
                        "reasoning_content": "LOCAL",
                        "_openai_tool_extra_content": {"c1": {"trace": "LOCAL-CALL"}},
                    },
                ),
                ChatMessage("tool", (ToolResultPart("c1", "evidence"),)),
            ),
            tools=(TOOL,),
        )
        payload = serialize_request(request, "model", True)
        self.assertEqual(payload["model"], "model")
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "system"})
        image_url = payload["messages"][1]["content"][1]["image_url"]["url"]
        self.assertEqual(
            image_url,
            "data:image/png;base64," + base64.b64encode(raw).decode("ascii"),
        )
        self.assertEqual(payload["messages"][2]["reasoning_content"], "LOCAL")
        self.assertEqual(
            payload["messages"][2]["tool_calls"][0]["extra_content"],
            {"trace": "LOCAL-CALL"},
        )
        self.assertEqual(payload["messages"][3]["role"], "tool")
        self.assertEqual(payload["messages"][3]["tool_call_id"], "c1")
        self.assertEqual(payload["tools"][0]["function"]["name"], "fetch_url")

    def test_serializer_adds_provider_routing_preferences(self) -> None:
        request = ChatRequest(messages=(ChatMessage("user", (TextPart("hi"),)),))
        payload = serialize_request(
            request,
            "model",
            False,
            provider_preferences=("deepinfra", "together"),
        )
        self.assertEqual(
            payload["provider"],
            {"only": ["deepinfra", "together"], "order": ["deepinfra", "together"]},
        )

    def test_serializer_omits_provider_without_preferences(self) -> None:
        request = ChatRequest(messages=(ChatMessage("user", (TextPart("hi"),)),))
        self.assertNotIn("provider", serialize_request(request, "model", False))

    def test_provider_state_is_not_synthesized_when_absent(self) -> None:
        request = ChatRequest(
            messages=(
                ChatMessage(
                    "assistant",
                    (ToolCallPart("c1", "fetch_url", {"url": "x"}),),
                ),
            ),
            tools=(TOOL,),
        )
        message = serialize_request(request, "model", True)["messages"][0]
        self.assertNotIn("reasoning_content", message)
        self.assertNotIn("extra_content", message["tool_calls"][0])

    def test_parser_handles_string_and_list_text(self) -> None:
        string = parse_response(
            {"choices": [{"message": {"content": "hello"}}]},
            (),
            "demo",
        )
        listed = parse_response(
            {"choices": [{"message": {"content": [{"text": "a"}, {"text": "b"}]}}]},
            (),
            "demo",
        )
        self.assertEqual(string.content, "hello")
        self.assertEqual(listed.content, "ab")

    def test_parser_maps_structured_call_and_replay_metadata_to_opaque_state(self) -> None:
        response = parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "LOCAL",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "fetch_url",
                                        "arguments": '{"url":"https://example.com"}',
                                    },
                                    "extra_content": {"trace": "CALL-LOCAL"},
                                }
                            ],
                        }
                    }
                ]
            },
            (TOOL,),
            "demo",
        )
        self.assertEqual(response.tool_calls[0].name, "fetch_url")
        self.assertEqual(
            dict(response.tool_calls[0].arguments),
            {"url": "https://example.com"},
        )
        self.assertEqual(response.provider_state["reasoning_content"], "LOCAL")
        self.assertEqual(
            response.provider_state["_openai_tool_extra_content"]["c1"],
            {"trace": "CALL-LOCAL"},
        )

    def test_parser_supports_narrow_text_tool_call_fallback(self) -> None:
        response = parse_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "<tool_call>fetch_url"
                                "<arg_key>url</arg_key>"
                                "<arg_value>https://example.com</arg_value>"
                                "</tool_call>"
                            )
                        }
                    }
                ]
            },
            (TOOL,),
            "demo",
        )
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].name, "fetch_url")
        self.assertIsNone(response.content)

    def test_parser_rejects_malformed_tool_arguments(self) -> None:
        with self.assertRaisesRegex(ProviderError, "JSON"):
            parse_response(
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {
                                            "name": "fetch_url",
                                            "arguments": "{bad",
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                (TOOL,),
                "demo",
            )


def _session_target(driver_options: dict[str, object]):
    spec = TargetSpec(
        identity=ProviderTargetIdentity(
            family="opencode",
            route="text",
            model="deepseek-v4.1-flash",
            credential_id="cred-1",
            target_id="opencode:text:m1:c1",
        ),
        driver="openai-chat",
        capabilities=ProviderCapabilities(route="text"),
        base_url="https://opencode.ai/zen/go/v1",
        request_timeout_sec=5.0,
        recovery_policy=RecoveryPolicy(overrides=MappingProxyType({})),
        driver_options=driver_options,
    )
    return OpenAIChatDriver().build_target(spec, credential="test-key")


async def _sent_session_headers(
    target, calls: int, *, session_id: str | None = None
) -> list[str | None]:
    seen: list[str | None] = []

    def responder(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("x-opencode-session"))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    target._client = httpx.AsyncClient(
        base_url="https://example.org/v1/",
        transport=httpx.MockTransport(responder),
        headers=target._client.headers,
    )
    request = ChatRequest(
        messages=(ChatMessage("user", (TextPart("hi"),)),),
        session_id=session_id,
    )
    for _ in range(calls):
        await target.chat(request)
    await target.aclose()
    return seen


class OpenCodeSessionHeaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_target_reuses_one_opaque_session_header(self) -> None:
        target = _session_target({"session_header": "x-opencode-session"})
        stable = target._client.headers.get("x-opencode-session", "")
        self.assertTrue(stable.startswith("ses_"))
        self.assertEqual(await _sent_session_headers(target, 2), [stable, stable])

    async def test_target_without_session_option_sends_no_session_header(self) -> None:
        target = _session_target({})
        self.assertEqual(await _sent_session_headers(target, 2), [None, None])

    async def test_conversation_id_overrides_target_fallback(self) -> None:
        target = _session_target({"session_header": "x-opencode-session"})
        fallback = target._client.headers.get("x-opencode-session", "")
        seen = await _sent_session_headers(
            target, 3, session_id="ses_conversation_alpha"
        )
        self.assertNotIn(fallback, ("", "ses_conversation_alpha"))
        self.assertEqual(seen, ["ses_conversation_alpha"] * 3)

    async def test_distinct_conversations_send_distinct_session_ids(self) -> None:
        target = _session_target({"session_header": "x-opencode-session"})
        first = await _sent_session_headers(
            target, 1, session_id="ses_conversation_alpha"
        )
        second = await _sent_session_headers(
            target, 1, session_id="ses_conversation_beta"
        )
        self.assertEqual(first, ["ses_conversation_alpha"])
        self.assertEqual(second, ["ses_conversation_beta"])

    def test_opencode_profile_declares_session_header_and_user_agent(self) -> None:
        options = load_provider_catalog().require("opencode").driver_options
        self.assertEqual(options.get("session_header"), "x-opencode-session")
        self.assertEqual(
            options.get("extra_headers", {}).get("User-Agent"),
            "fucking-cool-ai-bot/1.0",
        )


class ProviderSessionIdTests(unittest.TestCase):
    def test_id_is_stable_per_conversation_and_distinct_between_them(self) -> None:
        self.assertEqual(provider_session_id(42), provider_session_id(42))
        self.assertEqual(provider_session_id((42, 7)), provider_session_id((42, 7)))
        self.assertNotEqual(provider_session_id(42), provider_session_id(43))
        self.assertNotEqual(provider_session_id((42, 7)), provider_session_id((42, 8)))
        self.assertNotEqual(provider_session_id(42), provider_session_id((42, 0)))

    def test_id_is_opaque_and_header_safe(self) -> None:
        value = provider_session_id(987654321)
        self.assertTrue(value.startswith("ses_"))
        self.assertNotIn("987654321", value)
        ChatRequest(
            messages=(ChatMessage("user", (TextPart("hi"),)),),
            session_id=value,
        )

    def test_new_id_is_fresh_opaque_and_header_safe(self) -> None:
        first = new_provider_session_id()
        second = new_provider_session_id()
        self.assertTrue(first.startswith("ses_"))
        self.assertEqual(len(first), 36)
        self.assertNotEqual(first, second)
        ChatRequest(
            messages=(ChatMessage("user", (TextPart("hi"),)),),
            session_id=first,
        )

    def test_chat_request_rejects_header_unsafe_session_id(self) -> None:
        with self.assertRaises(ValueError):
            ChatRequest(
                messages=(ChatMessage("user", (TextPart("hi"),)),),
                session_id="ses_bad value\r\nX-Injected: 1",
            )


if __name__ == "__main__":
    unittest.main()
