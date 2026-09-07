"""Lớp AI provider — OpenAI-compatible, tool-calling, fallback."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx


class ProviderError(Exception):
    """Lỗi khi gọi một provider cụ thể."""

    def __init__(self, message: str, *, unsupported_tools: bool = False) -> None:
        super().__init__(message)
        self.unsupported_tools = unsupported_tools


class AllProvidersFailed(Exception):
    """Tất cả provider trong chuỗi đều thất bại."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict  # đã parse từ JSON


@dataclass
class ChatResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class OpenAICompatProvider:
    """Provider nói chung giao thức OpenAI chat/completions (HTTP trực tiếp)."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.supports_tools = True
        headers = {"Authorization": f"Bearer {api_key}"}
        if extra_headers:
            headers.update(extra_headers)
        # Dùng URL tương đối (không mở đầu '/') để giữ nguyên path prefix của base_url
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers=headers,
            timeout=timeout,
        )

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResponse:
        payload: dict = {"model": self.model, "messages": messages}
        if tools and self.supports_tools:
            payload["tools"] = tools

        resp = await self._client.post("chat/completions", json=payload)
        if resp.status_code >= 400:
            body = resp.text[:500]
            unsupported = (
                resp.status_code == 400
                and ("tool" in body.lower() or "function" in body.lower())
            )
            raise ProviderError(
                f"{self.name} HTTP {resp.status_code}: {body}",
                unsupported_tools=unsupported,
            )

        data = resp.json()
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"{self.name}: phản hồi thiếu choices: {str(data)[:200]}") from exc

        content = msg.get("content")
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                ToolCall(id=tc.get("id") or "", name=fn.get("name") or "", arguments=args)
            )
        return ChatResponse(content=content, tool_calls=tool_calls)

    async def aclose(self) -> None:
        await self._client.aclose()
