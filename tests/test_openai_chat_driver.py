from __future__ import annotations

import base64
import unittest

from app.ai.base import ProviderError
from app.ai.contracts import (
    ChatMessage,
    ChatRequest,
    ImagePart,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
)
from app.ai.drivers.openai_chat import parse_response, serialize_request


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


if __name__ == "__main__":
    unittest.main()
