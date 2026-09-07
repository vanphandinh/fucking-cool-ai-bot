"""Router AI: chạy vòng tool-calling + tự fallback giữa các provider."""
from __future__ import annotations

import logging
from copy import deepcopy
from typing import Awaitable, Callable

from ..config import Settings
from .base import (
    AllProvidersFailed,
    ChatResponse,
    OpenAICompatProvider,
    ProviderError,
)
from .gemini import make_gemini_provider
from .groq import make_groq_provider
from .openrouter import make_openrouter_provider

logger = logging.getLogger(__name__)

ToolExecutor = Callable[[str, dict], Awaitable[str]]


class AIProviderRouter:
    def __init__(self, providers: list[OpenAICompatProvider], max_tool_rounds: int = 4) -> None:
        self.providers = providers
        self.max_tool_rounds = max_tool_rounds

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
    ) -> tuple[str, str]:
        """Gọi lần lượt từng provider; provider nào xong trước thì dùng.

        Trả về (text, provider_name). Ném AllProvidersFailed khi tất cả lỗi.
        """
        last_error: ProviderError | None = None

        for provider in self.providers:
            # pass 0: có tools; nếu provider báo không hỗ trợ tools -> pass 1 không tools
            for pass_no in (0, 1):
                if pass_no == 1 and not last_error:
                    break  # pass 0 không lỗi liên quan tools thì không cần pass 1
                local_msgs = deepcopy(messages)
                use_tools = tools if (provider.supports_tools and pass_no == 0) else None
                try:
                    text, _ = await self._complete_with_provider(
                        provider, local_msgs, use_tools, tool_executor
                    )
                    return text, provider.name
                except ProviderError as exc:
                    last_error = exc
                    if exc.unsupported_tools and provider.supports_tools:
                        provider.supports_tools = False
                        continue  # thử lại cùng provider, không kèm tools
                    break  # lỗi khác -> chuyển provider kế tiếp
                except Exception as exc:  # lỗi không lường trước -> coi như hỏng provider này
                    last_error = ProviderError(f"{provider.name}: {exc}")
                    break

        raise AllProvidersFailed(str(last_error) if last_error else "Tất cả provider đều lỗi")

    async def _complete_with_provider(
        self,
        provider: OpenAICompatProvider,
        messages: list[dict],
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
    ) -> tuple[str, bool]:
        for _round in range(self.max_tool_rounds + 1):
            resp = await provider.chat(messages, tools)
            if not resp.tool_calls:
                text = (resp.content or "").strip()
                if not text:
                    raise ProviderError(f"{provider.name}: model trả về nội dung rỗng")
                return text, False

            # Thực thi tool-calls
            messages.append(_assistant_tool_message(resp))
            for tc in resp.tool_calls:
                try:
                    output = await tool_executor(tc.name, tc.arguments)
                except Exception as exc:  # noqa: BLE001 — lỗi tool không được làm sập bot
                    output = f"Lỗi khi chạy tool '{tc.name}': {exc}"
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": output[:6000]}
                )

        raise ProviderError(f"{provider.name}: vượt quá {self.max_tool_rounds} vòng gọi tool")


def _assistant_tool_message(resp: ChatResponse) -> dict:
    return {
        "role": "assistant",
        "content": resp.content or "",
        "tool_calls": [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": _json_dumps(tc.arguments)},
            }
            for tc in resp.tool_calls
        ],
    }


def _json_dumps(data: dict) -> str:
    import json

    return json.dumps(data, ensure_ascii=False)


def build_provider_router(settings: Settings) -> AIProviderRouter:
    """Dựng chuỗi provider theo thứ tự ưu tiên, chỉ giữ provider có key."""
    providers: list[OpenAICompatProvider] = []
    if settings.gemini_api_key:
        providers.append(make_gemini_provider(settings))
    if settings.groq_api_key:
        providers.append(make_groq_provider(settings))
    if settings.openrouter_api_key:
        providers.append(make_openrouter_provider(settings))
    return AIProviderRouter(providers, max_tool_rounds=settings.max_tool_rounds)
