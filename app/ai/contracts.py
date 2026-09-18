"""Protocol-independent canonical AI chat contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


_EMPTY_MAPPING = MappingProxyType({})


def _freeze_mapping(value: Mapping[str, object] | None) -> Mapping[str, object]:
    if not value:
        return _EMPTY_MAPPING
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True, repr=False)
class ImagePart:
    mime_type: str
    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("ImagePart.data must be bytes")

    def __repr__(self) -> str:
        return (
            f"ImagePart(mime_type={self.mime_type!r}, "
            f"data=<raw bytes: {len(self.data)}>)"
        )


@dataclass(frozen=True, slots=True)
class ToolCallPart:
    id: str
    name: str
    arguments: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class ToolResultPart:
    tool_call_id: str
    content: str


MessagePart = TextPart | ImagePart | ToolCallPart | ToolResultPart


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    parts: tuple[MessagePart, ...]
    provider_state: Mapping[str, object] = field(default_factory=lambda: _EMPTY_MAPPING)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", tuple(self.parts))
        object.__setattr__(
            self,
            "provider_state",
            _freeze_mapping(self.provider_state),
        )

    def portable(self) -> "ChatMessage":
        if not self.provider_state:
            return self
        return ChatMessage(role=self.role, parts=self.parts)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        frozen = _freeze_mapping(self.parameters)
        if frozen.get("type") != "object":
            raise ValueError("ToolDefinition.parameters must be an object schema")
        object.__setattr__(self, "parameters", frozen)


@dataclass(frozen=True, slots=True)
class ChatRequest:
    messages: tuple[ChatMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(self.messages))
        object.__setattr__(self, "tools", tuple(self.tools))


@dataclass(frozen=True, slots=True)
class ChatResponse:
    content: str | None = None
    tool_calls: tuple[ToolCallPart, ...] = ()
    provider_state: Mapping[str, object] = field(default_factory=lambda: _EMPTY_MAPPING)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_calls", tuple(self.tool_calls))
        object.__setattr__(
            self,
            "provider_state",
            _freeze_mapping(self.provider_state),
        )
