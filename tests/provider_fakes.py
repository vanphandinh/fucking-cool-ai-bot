"""Reusable generic provider fakes for router regressions."""

from __future__ import annotations

from copy import deepcopy

from app.ai.base import ChatResponse, ToolCall
from app.ai.capabilities import ProviderCapabilities
from app.ai.health import ProviderHealth


class ScriptedProvider:
    def __init__(
        self,
        name: str,
        script: list[ChatResponse | Exception],
        *,
        route: str = "text",
        max_images: int = 0,
    ) -> None:
        self.name = name
        self.model = f"{name}-model"
        self.supports_tools = True
        self.capabilities = ProviderCapabilities(
            route=route,
            supports_vision=route == "vision",
            max_images=max_images if route == "vision" else 0,
        )
        self.health = ProviderHealth()
        self.script = list(script)
        self.calls: list[tuple[list[dict], list[dict] | None]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        self.calls.append((deepcopy(messages), deepcopy(tools)))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:
        return None


async def noop_tool(_name: str, _args: dict) -> str:
    return "unused"


def fetch_url_tool() -> dict:
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


def tool_response(count: int, prefix: str = "call") -> ChatResponse:
    return ChatResponse(
        tool_calls=[
            ToolCall(
                id=f"{prefix}_{index}",
                name="fetch_url",
                arguments={"url": f"https://example.com/{prefix}-{index}"},
            )
            for index in range(count)
        ]
    )
