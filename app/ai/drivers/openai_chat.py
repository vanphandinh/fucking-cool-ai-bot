"""OpenAI chat-completions protocol driver.

All OpenAI wire serialization/parsing lives in this module.  Canonical domain
objects keep raw image bytes, semantic tool calls/results, and opaque
provider-local replay state.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping

import httpx

from ..base import ProviderError, TransportFailureKind
from ..contracts import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ImagePart,
    TextPart,
    ToolCallPart,
    ToolDefinition,
    ToolResultPart,
)
from ..health import ProviderHealth
from ..target import TargetSpec

_TOOL_MARKUP_HINT_RE = re.compile(
    r"</?(?:tool_call|arg_key|arg_value)>|<\s*/?\s*[|｜]\s*/?dsml[|｜]",
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
_TOOL_EXTRA_STATE_KEY = "_openai_tool_extra_content"
_AI_CONNECT_TIMEOUT_SEC = 8.0
_AI_WRITE_TIMEOUT_SEC = 20.0
_AI_POOL_TIMEOUT_SEC = 5.0


def _contains_internal_tool_markup(value: object) -> bool:
    return bool(_TOOL_MARKUP_HINT_RE.search(str(value or "")))


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
    return delay if delay >= 0 else None


def _transport_failure_kind(exc: httpx.HTTPError) -> TransportFailureKind | None:
    if isinstance(exc, httpx.ConnectTimeout):
        return TransportFailureKind.CONNECT_TIMEOUT
    if isinstance(exc, httpx.ReadTimeout):
        return TransportFailureKind.READ_TIMEOUT
    if isinstance(exc, httpx.WriteTimeout):
        return TransportFailureKind.WRITE_TIMEOUT
    if isinstance(exc, httpx.PoolTimeout):
        return TransportFailureKind.POOL_TIMEOUT
    if isinstance(exc, httpx.ConnectError):
        return TransportFailureKind.CONNECT_ERROR
    return None


def _transport_error_detail(exc: httpx.HTTPError, read_timeout: float) -> str:
    # HTTPX exception text may echo request metadata such as invalid header values.
    # Keep diagnostics structural so credential-bearing request data cannot escape.
    parts = [type(exc).__name__]
    if isinstance(exc, httpx.ReadTimeout):
        parts.append(f"read_timeout={read_timeout:g}s")
    elif isinstance(exc, httpx.ConnectTimeout):
        parts.append(f"connect_timeout={_AI_CONNECT_TIMEOUT_SEC:g}s")
    elif isinstance(exc, httpx.WriteTimeout):
        parts.append(f"write_timeout={_AI_WRITE_TIMEOUT_SEC:g}s")
    elif isinstance(exc, httpx.PoolTimeout):
        parts.append(f"pool_timeout={_AI_POOL_TIMEOUT_SEC:g}s")
    return ": ".join(parts)


def _tool_names(tools: tuple[ToolDefinition, ...]) -> set[str]:
    return {tool.name for tool in tools}


def _parse_structured_tool_calls(
    message: Mapping[str, object],
    tools: tuple[ToolDefinition, ...],
    *,
    provider_name: str,
) -> tuple[tuple[ToolCallPart, ...], dict[str, object]]:
    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        return (), {}
    if not isinstance(raw_calls, list):
        raise ProviderError(
            f"{provider_name}: tool_calls phải là array",
            transient=False,
        )

    allowed_names = _tool_names(tools)
    parsed: list[ToolCallPart] = []
    extra_by_id: dict[str, object] = {}
    for raw_call in raw_calls:
        if not isinstance(raw_call, dict):
            raise ProviderError(
                f"{provider_name}: structured tool call không phải object",
                transient=False,
            )
        if raw_call.get("type") not in (None, "function"):
            raise ProviderError(
                f"{provider_name}: structured tool call có type không hợp lệ",
                transient=False,
            )
        call_id = str(raw_call.get("id") or "").strip()
        if not call_id:
            raise ProviderError(
                f"{provider_name}: structured tool call thiếu id",
                transient=False,
            )
        function = raw_call.get("function")
        if not isinstance(function, dict):
            raise ProviderError(
                f"{provider_name}: structured tool call thiếu function object",
                transient=False,
            )
        name = str(function.get("name") or "").strip()
        if not name:
            raise ProviderError(
                f"{provider_name}: structured tool call thiếu function name",
                transient=False,
            )
        if name not in allowed_names:
            raise ProviderError(
                f"{provider_name}: model gọi tool ngoài active schema: {name}",
                transient=False,
            )

        raw_args = function.get("arguments")
        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            argument_error: ProviderError | None = None
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                argument_error = ProviderError(
                    f"{provider_name}: tool arguments không phải JSON hợp lệ",
                    transient=False,
                )
            if argument_error is not None:
                raise argument_error
        else:
            raise ProviderError(
                f"{provider_name}: tool arguments phải là JSON object",
                transient=False,
            )
        if not isinstance(args, dict):
            raise ProviderError(
                f"{provider_name}: tool arguments phải là JSON object",
                transient=False,
            )
        parsed.append(ToolCallPart(call_id, name, args))
        extra = raw_call.get("extra_content")
        if isinstance(extra, dict):
            extra_by_id[call_id] = deepcopy(extra)
    return tuple(parsed), extra_by_id


def _parse_text_tool_call(
    content: str,
    tools: tuple[ToolDefinition, ...],
) -> ToolCallPart | None:
    match = _TEXT_TOOL_CALL_RE.fullmatch(content)
    if not match:
        return None
    name = match.group(1).strip()
    if name not in _tool_names(tools):
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
    return ToolCallPart(
        id=f"call_{uuid.uuid4().hex[:24]}",
        name=name,
        arguments=args,
    )


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


def parse_response(
    data: dict,
    active_tools: tuple[ToolDefinition, ...],
    provider_name: str,
) -> ChatResponse:
    """Parse one OpenAI chat-completions response into canonical domain state."""

    response_error: ProviderError | None = None
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        response_error = ProviderError(f"{provider_name}: phản hồi thiếu choices")
        message = None
    if response_error is not None:
        raise response_error
    if not isinstance(message, dict):
        raise ProviderError(f"{provider_name}: message không phải object")

    content = _normalize_content(message.get("content"))
    tool_calls, extras = _parse_structured_tool_calls(
        message,
        active_tools,
        provider_name=provider_name,
    )
    if content and _contains_internal_tool_markup(content):
        if tool_calls:
            raise ProviderError(
                f"{provider_name}: model trả internal tool markup kèm structured tool_calls",
                transient=False,
            )
        parsed = _parse_text_tool_call(content, active_tools)
        if parsed is None:
            raise ProviderError(
                f"{provider_name}: model trả tool-call dạng text không hợp lệ",
                transient=False,
            )
        tool_calls = (parsed,)
        content = None

    state = {
        key: deepcopy(message[key])
        for key in _ASSISTANT_REPLAY_FIELDS
        if key in message
    }
    if extras:
        state[_TOOL_EXTRA_STATE_KEY] = extras
    return ChatResponse(
        content=content,
        tool_calls=tool_calls,
        provider_state=state,
    )


def _serialize_tool_definition(tool: ToolDefinition) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
        },
    }


def _serialize_message(message: ChatMessage) -> dict:
    text_parts = [part.text for part in message.parts if isinstance(part, TextPart)]
    image_parts = [part for part in message.parts if isinstance(part, ImagePart)]
    calls = [part for part in message.parts if isinstance(part, ToolCallPart)]
    results = [part for part in message.parts if isinstance(part, ToolResultPart)]

    if results:
        if len(message.parts) != 1 or len(results) != 1:
            raise ValueError("tool result message must contain exactly one ToolResultPart")
        result = results[0]
        return {
            "role": "tool",
            "tool_call_id": result.tool_call_id,
            "content": result.content,
        }

    out: dict[str, object] = {"role": message.role}
    if image_parts:
        content: list[dict[str, object]] = []
        for part in message.parts:
            if isinstance(part, TextPart):
                content.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                encoded = base64.b64encode(part.data).decode("ascii")
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{part.mime_type};base64,{encoded}",
                        },
                    }
                )
            elif not isinstance(part, ToolCallPart):
                raise ValueError(f"unsupported OpenAI message part: {type(part).__name__}")
        out["content"] = content
    else:
        out["content"] = "".join(text_parts)

    if calls:
        extras = message.provider_state.get(_TOOL_EXTRA_STATE_KEY)
        extra_by_id = extras if isinstance(extras, Mapping) else {}
        serialized_calls: list[dict[str, object]] = []
        for call in calls:
            wire_call: dict[str, object] = {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(dict(call.arguments), ensure_ascii=False),
                },
            }
            extra = extra_by_id.get(call.id)
            if isinstance(extra, Mapping):
                wire_call["extra_content"] = deepcopy(dict(extra))
            serialized_calls.append(wire_call)
        out["tool_calls"] = serialized_calls

    for key in _ASSISTANT_REPLAY_FIELDS:
        if key in message.provider_state:
            out[key] = deepcopy(message.provider_state[key])
    return out


def serialize_request(
    request: ChatRequest,
    model: str,
    supports_tools: bool,
) -> dict:
    """Serialize canonical request state to OpenAI chat-completions JSON."""

    payload: dict[str, object] = {
        "model": model,
        "messages": [_serialize_message(message) for message in request.messages],
    }
    if request.tools and supports_tools:
        payload["tools"] = [_serialize_tool_definition(tool) for tool in request.tools]
    return payload



class OpenAIChatTarget:
    """Concrete target speaking the OpenAI chat-completions wire protocol."""

    def __init__(self, spec: TargetSpec, *, credential: str) -> None:
        self.spec = spec
        self.health = ProviderHealth()
        self.explicit_stream = spec.driver_options.get("explicit_stream", False)
        self._request_timeout = spec.request_timeout_sec
        headers = {"Authorization": f"Bearer {credential.strip()}"}
        extra_headers = spec.driver_options.get("extra_headers")
        if isinstance(extra_headers, dict):
            headers.update({str(k): str(v) for k, v in extra_headers.items()})
        request_timeout = httpx.Timeout(
            connect=_AI_CONNECT_TIMEOUT_SEC,
            read=spec.request_timeout_sec,
            write=_AI_WRITE_TIMEOUT_SEC,
            pool=_AI_POOL_TIMEOUT_SEC,
        )
        self._client = httpx.AsyncClient(
            base_url=spec.base_url.rstrip("/") + "/",
            headers=headers,
            timeout=request_timeout,
        )

    @property
    def name(self) -> str:
        return self.spec.identity.family

    @property
    def model(self) -> str:
        return self.spec.identity.model

    @property
    def capabilities(self):
        return self.spec.capabilities

    @property
    def supports_tools(self) -> bool:
        return self.spec.capabilities.supports_tools

    @property
    def credential_id(self) -> str:
        return self.spec.identity.credential_id

    @property
    def target_id(self) -> str:
        return self.spec.identity.target_id

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Send one canonical request through the OpenAI chat-completions protocol."""

        payload = serialize_request(request, self.model, self.supports_tools)
        if self.explicit_stream is not None:
            payload["stream"] = self.explicit_stream

        transport_error: ProviderError | None = None
        try:
            response = await self._client.post("chat/completions", json=payload)
        except httpx.HTTPError as exc:
            transport_error = ProviderError(
                f"{self.name}: lỗi mạng "
                f"({_transport_error_detail(exc, self._request_timeout)})",
                transient=True,
                transport_kind=_transport_failure_kind(exc),
            )
        if transport_error is not None:
            raise transport_error

        if response.status_code >= 400:
            error_message = response.text
            generation_error = False
            failed_generation_has_markup = False
            try:
                error_data = response.json()
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
            tool_error = response.status_code == 400 and bool(request.tools) and (
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
            retry_after = _parse_retry_after(response.headers.get("retry-after"))
            transient = response.status_code == 429 or response.status_code >= 500
            raise ProviderError(
                f"{self.name} HTTP {response.status_code}",
                unsupported_tools=unsupported,
                retry_without_tools=(
                    tool_error and not unsupported and not failed_generation_has_markup
                ),
                status_code=response.status_code,
                retry_after=retry_after,
                transient=transient,
            )

        response_error: ProviderError | None = None
        try:
            data = response.json()
        except json.JSONDecodeError:
            response_error = ProviderError(f"{self.name}: phản hồi không phải JSON")
        if response_error is not None:
            raise response_error
        if not isinstance(data, dict):
            raise ProviderError(f"{self.name}: phản hồi JSON không phải object")
        return parse_response(data, request.tools, self.name)

    async def aclose(self) -> None:
        await self._client.aclose()


class OpenAIChatDriver:
    id = "openai-chat"

    def build_target(self, spec: TargetSpec, *, credential: str) -> OpenAIChatTarget:
        return OpenAIChatTarget(spec, credential=credential)
