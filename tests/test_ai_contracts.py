from __future__ import annotations

import unittest

from app.ai.contracts import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
)


class AIContractsTests(unittest.TestCase):
    def test_image_part_keeps_raw_bytes_and_repr_is_not_base64(self) -> None:
        part = ImagePart(mime_type="image/png", data=b"\x89PNG")
        self.assertEqual(part.data, b"\x89PNG")
        self.assertNotIn("base64", repr(part).lower())
        self.assertIn("raw bytes", repr(part))

    def test_image_part_rejects_non_bytes(self) -> None:
        with self.assertRaisesRegex(TypeError, "must be bytes"):
            ImagePart(mime_type="image/png", data="encoded")  # type: ignore[arg-type]

    def test_tool_definition_requires_object_schema(self) -> None:
        definition = ToolDefinition(
            name="fetch_url",
            description="Fetch one URL",
            parameters={"type": "object", "properties": {}},
        )
        self.assertEqual(definition.parameters["type"], "object")
        with self.assertRaisesRegex(ValueError, "object schema"):
            ToolDefinition(
                name="bad",
                description="bad",
                parameters={"type": "string"},
            )

    def test_message_parts_are_immutable_tuple(self) -> None:
        source = [TextPart("hello")]
        message = ChatMessage(role="user", parts=source)
        source.append(TextPart("later"))
        self.assertEqual(message.parts, (TextPart("hello"),))
        with self.assertRaises(AttributeError):
            message.parts.append(TextPart("no"))  # type: ignore[attr-defined]

    def test_provider_state_is_opaque_and_portable_copy_drops_only_state(self) -> None:
        parts = (
            TextPart("answer"),
            ToolCallPart("c1", "fetch_url", {"url": "https://example.com"}),
            ToolResultPart("c1", "evidence"),
        )
        message = ChatMessage(
            role="assistant",
            parts=parts,
            provider_state={"reasoning_details": [{"opaque": True}]},
        )
        portable = message.portable()
        self.assertEqual(portable.parts, parts)
        self.assertEqual(dict(portable.provider_state), {})
        self.assertNotEqual(dict(message.provider_state), {})

    def test_chat_request_tools_are_immutable_tuple(self) -> None:
        tool = ToolDefinition(
            name="fetch_url",
            description="Fetch URL",
            parameters={"type": "object", "properties": {}},
        )
        source = [tool]
        request = ChatRequest(
            messages=(ChatMessage("user", (TextPart("go"),)),),
            tools=source,
        )
        source.clear()
        self.assertEqual(request.tools, (tool,))

    def test_chat_response_uses_canonical_tool_calls_and_opaque_state(self) -> None:
        call = ToolCallPart("c1", "fetch_url", {"url": "https://example.com"})
        response = ChatResponse(
            content="",
            tool_calls=(call,),
            provider_state={"reasoning_content": "opaque"},
        )
        self.assertEqual(response.tool_calls, (call,))
        self.assertEqual(response.provider_state["reasoning_content"], "opaque")


if __name__ == "__main__":
    unittest.main()
