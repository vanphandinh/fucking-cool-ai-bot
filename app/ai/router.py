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

# Một phản hồi model có thể chứa rất nhiều tool call trong cùng một lượt. Nếu
# không chặn, một câu hỏi duy nhất có thể đốt quota search/reader và giữ chat
# lock hàng phút dù MAX_TOOL_ROUNDS đã nhỏ (giới hạn đó chỉ đếm số *lượt*).
_MAX_TOOL_CALLS_TOTAL = 8


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
            # pass 0: có tools; pass 1 (chỉ khi pass 0 lỗi vì tools): không kèm tools
            already_plain = False
            for pass_no in (0, 1):
                local_msgs = deepcopy(messages)
                use_tools = tools if (provider.supports_tools and pass_no == 0) else None
                if use_tools is None and already_plain:
                    break  # pass 0 đã chạy không-tools — không gọi trùng
                if use_tools is None:
                    already_plain = True
                try:
                    text = await self._complete_with_provider(
                        provider, local_msgs, use_tools, tool_executor
                    )
                    return text, provider.name
                except ProviderError as exc:
                    last_error = exc
                    logger.warning("Provider %s lỗi: %s", provider.name, exc)
                    # Giữ transcript tool đã chạy: retry không-tools / provider kế
                    # phải thấy kết quả search, không tìm lại từ đầu.
                    if len(local_msgs) > len(messages):
                        messages = local_msgs
                    if exc.unsupported_tools and provider.supports_tools:
                        # Model không hỗ trợ tool-calling -> tắt vĩnh viễn rồi thử lại
                        provider.supports_tools = False
                        continue
                    if exc.retry_without_tools:
                        # Lỗi chỉ xảy ra khi tool-calling (vd kẹt vòng lặp gọi tool):
                        # thử lại 1 lần không kèm tools, vẫn giữ nguyên supports_tools
                        continue
                    break  # lỗi khác -> chuyển provider kế tiếp
                except Exception as exc:  # lỗi không lường trước -> coi như hỏng provider này
                    last_error = ProviderError(f"{provider.name}: {exc}")
                    logger.warning("Provider %s lỗi không lường trước: %s", provider.name, exc)
                    if len(local_msgs) > len(messages):
                        messages = local_msgs
                    break

        raise AllProvidersFailed(str(last_error) if last_error else "Tất cả provider đều lỗi")

    async def _complete_with_provider(
        self,
        provider: OpenAICompatProvider,
        messages: list[dict],
        tools: list[dict] | None,
        tool_executor: ToolExecutor,
    ) -> str:
        # Vòng 0 là lượt trả lời đầu tiên; mỗi vòng sau tương ứng 1 lượt thực thi
        # tool-call. Cho phép tối đa max_tool_rounds lượt và một trần tổng số call
        # riêng để chống phản hồi fan-out bất thường từ model.
        executed_tool_calls = 0
        for _round in range(self.max_tool_rounds + 1):
            resp = await provider.chat(messages, tools)
            if not resp.tool_calls:
                text = (resp.content or "").strip()
                if not text:
                    raise ProviderError(f"{provider.name}: model trả về nội dung rỗng")
                return text
            if _round >= self.max_tool_rounds:
                raise ProviderError(
                    f"{provider.name}: model gọi tool quá {self.max_tool_rounds} "
                    "vòng — dừng để tránh kẹt vòng lặp",
                    retry_without_tools=True,
                )
            if executed_tool_calls + len(resp.tool_calls) > _MAX_TOOL_CALLS_TOTAL:
                raise ProviderError(
                    f"{provider.name}: model yêu cầu quá {_MAX_TOOL_CALLS_TOTAL} tool call "
                    "trong một câu hỏi — dừng để bảo vệ quota",
                    retry_without_tools=True,
                )

            # Thực thi tool-calls
            messages.append(_assistant_tool_message(resp))
            for tc in resp.tool_calls:
                try:
                    output = await tool_executor(tc.name, tc.arguments)
                except Exception as exc:  # noqa: BLE001 — lỗi tool không được làm sập bot
                    output = f"Lỗi khi chạy tool '{tc.name}': {exc}"
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": str(output)[:6000],
                    }
                )
            executed_tool_calls += len(resp.tool_calls)

        # Không thể chạm tới — vòng lặp luôn return/raise phía trên.
        raise ProviderError(f"{provider.name}: vòng lặp tool kết thúc bất thường")


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
