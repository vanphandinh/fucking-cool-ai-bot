"""Lớp AI provider — OpenAI-compatible, tool-calling, fallback."""

from __future__ import annotations

import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field

import httpx

from .capabilities import ProviderCapabilities
from .health import ProviderHealth

_IMAGE_DATA_URL_RE = re.compile(
    r"data:image/(?:jpeg|png|webp);base64,[A-Za-z0-9+/=_-]+",
    flags=re.IGNORECASE,
)
_TOOL_MARKUP_HINT_RE = re.compile(
    r"</?(?:tool_call|arg_key|arg_value)>",
    flags=re.IGNORECASE,
)
_TEXT_TOOL_CALL_RE = re.compile(
    r"^\s*<tool_call>\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*(.*?)</tool_call>\s*$",
    flags=re.IGNORECASE | re.DOTALL,
)
_TEXT_TOOL_ARG_RE = re.compile(
    r"<arg_key>\s*([^<]+?)\s*</arg_key>\s*<arg_value>(.*?)</arg_value>",
    flags=re.IGNORECASE | re.DOTALL,
)
_ASSISTANT_REPLAY_FIELDS = ("reasoning_details", "reasoning", "reasoning_content")


def _safe_error_excerpt(value: object, limit: int) -> str:
    """Redact image payloads before provider responses can reach logs/status."""
    text = value if isinstance(value, str) else str(value)
    redacted = _IMAGE_DATA_URL_RE.sub("data:image/[redacted];base64,[redacted]", text)
    return redacted[:limit]


def _contains_internal_tool_markup(value: object) -> bool:
    return bool(_TOOL_MARKUP_HINT_RE.search(str(value or "")))


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        unsupported_tools: bool = False,
        retry_without_tools: bool = False,
        status_code: int | None = None,
        retry_after: float | None = None,
        transient: bool = True,
    ) -> None:
        super().__init__(message)
        self.unsupported_tools = unsupported_tools
        self.retry_without_tools = retry_without_tools
        self.status_code = status_code
        self.retry_after = retry_after
        self.transient = transient


class AllProvidersFailed(Exception):
    pass


class NoCapableProvider(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    extra_content: dict | None = None


@dataclass
class ChatResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    assistant_metadata: dict = field(default_factory=dict)


def _allowed_tool_names(tools: list[dict] | None) -> set[str]:
    names: set[str] = set()
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function")
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _parse_text_tool_call(content: str, tools: list[dict] | None) -> ToolCall | None:
    """Parse the narrow text encoding emitted by some tool-capable providers.

    Only a whole-response block is accepted, and the requested tool must exist
    in the active tool schema. Anything else is rejected by the caller instead
    of being surfaced as assistant text.
    """
    match = _TEXT_TOOL_CALL_RE.fullmatch(content)
    if not match:
        return None

    name = match.group(1).strip()
    if name not in _allowed_tool_names(tools):
        return None

    body = match.group(2)
    args: dict[str, str] = {}
    for arg_match in _TEXT_TOOL_ARG_RE.finditer(body):
        key = arg_match.group(1).strip()
        if not key or key in args:
            return None
        args[key] = arg_match.group(2).strip()

    if _TEXT_TOOL_ARG_RE.sub("", body).strip():
        return None

    return ToolCall(
        id=f"call_{uuid.uuid4().hex[:24]}",
        name=name,
        arguments=args,
    )


class OpenAICompatProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        capabilities: ProviderCapabilities | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.supports_tools = True
        self.capabilities = capabilities or ProviderCapabilities()
        self.health = ProviderHealth()
        headers = {"Authorization": f"Bearer {api_key}"}
        if extra_headers:
            headers.update(extra_headers)
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/", headers=headers, timeout=timeout
        )

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> ChatResponse:
        payload: dict = {"model": self.model, "messages": messages}
        if tools and self.supports_tools:
            payload["tools"] = tools
        try:
            resp = await self._client.post("chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise ProviderError(f"{self.name}: lỗi mạng ({exc})", transient=True) from exc
        if resp.status_code >= 400:
            body = _safe_error_excerpt(resp.text, 500)
            error_message = resp.text
            generation_error = False
            failed_generation_has_markup = False
            try:
                error_data = resp.json()
            except ValueError:
                error_data = None
            if isinstance(error_data, dict) and isinstance(error_data.get("error"), dict):
                error = error_data["error"]
                error_message = str(error.get("message") or "")
                generation_error = (
                    error.get("code") == "tool_use_failed"
                    or "failed_generation" in error
                )
                failed_generation_has_markup = _contains_internal_tool_markup(
                    error.get("failed_generation")
                )
            tool_error = resp.status_code == 400 and bool(tools) and (
                generation_error
                or "tool" in error_message.lower()
                or "function" in error_message.lower()
            )
            unsupported = tool_error and not generation_error and bool(
                re.search(
                    r"(?:does not support|do not support|not support|unsupported)"
                    r"[^.\n]{0,60}(?:tool|function)"
                    r"|(?:tool|function)[^.\n]{0,60}(?:not supported|unsupported)",
                    error_message,
                    flags=re.IGNORECASE,
                )
            )
            retry_after = None
            raw_retry = resp.headers.get("retry-after")
            if raw_retry:
                try:
                    retry_after = float(raw_retry)
                except ValueError:
                    retry_after = None
            transient = resp.status_code == 429 or resp.status_code >= 500
            raise ProviderError(
                f"{self.name} HTTP {resp.status_code}: {body}",
                unsupported_tools=unsupported,
                retry_without_tools=(
                    tool_error and not unsupported and not failed_generation_has_markup
                ),
                status_code=resp.status_code,
                retry_after=retry_after,
                transient=transient,
            )
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"{self.name}: phản hồi không phải JSON: {_safe_error_excerpt(resp.text, 200)}"
            ) from exc
        try:
            msg = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"{self.name}: phản hồi thiếu choices: {_safe_error_excerpt(data, 200)}"
            ) from exc
        if not isinstance(msg, dict):
            raise ProviderError(
                f"{self.name}: message không phải object: {_safe_error_excerpt(msg, 200)}"
            )
        content = _normalize_content(msg.get("content"))
        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                fn = {}
            raw_args = fn.get("arguments")
            if isinstance(raw_args, dict):
                args = raw_args
            else:
                try:
                    args = json.loads(raw_args or "{}")
                except (json.JSONDecodeError, TypeError):
                    args = {}
            if not isinstance(args, dict):
                args = {}
            extra_content = tc.get("extra_content")
            if not isinstance(extra_content, dict):
                extra_content = None
            call_id = str(tc.get("id") or "").strip() or f"call_{uuid.uuid4().hex[:24]}"
            tool_calls.append(
                ToolCall(
                    id=call_id,
                    name=str(fn.get("name") or ""),
                    arguments=args,
                    extra_content=extra_content,
                )
            )

        if not tool_calls and content and _contains_internal_tool_markup(content):
            parsed = _parse_text_tool_call(content, tools)
            if parsed is None:
                raise ProviderError(
                    f"{self.name}: model trả tool-call dạng text không hợp lệ",
                    transient=False,
                )
            tool_calls.append(parsed)
            content = None

        assistant_metadata = {
            key: deepcopy(msg[key]) for key in _ASSISTANT_REPLAY_FIELDS if key in msg
        }
        return ChatResponse(
            content=content,
            tool_calls=tool_calls,
            assistant_metadata=assistant_metadata,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _normalize_content(content: object) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
        return "".join(parts)
    return str(content)
