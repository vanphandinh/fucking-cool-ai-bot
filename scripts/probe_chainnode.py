"""Live compatibility qualification for the Chainnode OpenAI-compatible gateway.

The probe is intentionally separate from runtime. It verifies the model catalog,
production non-stream Chat Completions, tool calling, tool continuation, and
no-tool discipline. Streaming is measured as a diagnostic only because the bot
runtime currently uses non-stream requests. Secrets are read from environment
variables and are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
import os
import random
import re
import sys
import time
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://dn.chainno.de/v1"
DEFAULT_MODELS = (
    "cl/z-ai/glm-5.3-flash",
    "cl/deepseek/deepseek-v4-flash",
    "cl/cline-free/muse-spark-1.3-contributor",
    "gweb/gemini-3.8-flash",
)
_TOOL_VALUE = "CHAINNODE_TOOL_PROBE"
TOOL_CONTINUATION_MARKER = "CHAINNODE_TOOL_PROBE_OK"
_TOOL = {
    "type": "function",
    "function": {
        "name": "echo_probe",
        "description": "Return the supplied probe value unchanged.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
}
_INTERNAL_MARKUP_RE = re.compile(
    r"<\s*(?:/?tool_call|/?arg_key|/?arg_value|[|｜]/?dsml[|｜])",
    flags=re.IGNORECASE,
)


def build_chat_payload(
    model: str,
    *,
    mode: str = "plain",
    stream: bool = False,
) -> dict[str, Any]:
    if mode == "plain":
        prompt = "Reply with exactly: CHAINNODE_PROBE_OK"
        include_tools = False
    elif mode == "tool":
        prompt = (
            "Call the echo_probe function with value CHAINNODE_TOOL_PROBE. "
            "Do not answer directly before calling the function."
        )
        include_tools = True
    elif mode == "no_tool":
        prompt = (
            "Reply with exactly CHAINNODE_NO_TOOL_OK. This request does not need any tool."
        )
        include_tools = True
    else:
        raise ValueError(f"unsupported probe mode: {mode}")

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an API compatibility probe."},
            {"role": "user", "content": prompt},
        ],
    }
    if include_tools:
        payload["tools"] = [_TOOL]
    if stream:
        payload["stream"] = True
    return payload


def extract_model_ids(data: object) -> set[str]:
    if not isinstance(data, dict):
        return set()
    items = data.get("data")
    if not isinstance(items, list):
        return set()
    out: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if model_id:
            out.add(model_id)
    return out


def _normalize_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text") is not None:
                parts.append(str(item["text"]))
        return "".join(parts)
    return "" if value is None else str(value)


def _has_internal_markup(value: object) -> bool:
    text = str(value or "")
    normalized = text.replace("｜", "|")
    return bool(_INTERNAL_MARKUP_RE.search(normalized))


def _parse_tool_call(message: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        return False, None
    call = calls[0]
    if not isinstance(call, dict):
        return False, None
    call_id = str(call.get("id") or "").strip()
    if not call_id or call.get("type") not in (None, "function"):
        return False, None
    function = call.get("function")
    if not isinstance(function, dict) or function.get("name") != "echo_probe":
        return False, None
    raw_arguments = function.get("arguments")
    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    elif isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return False, None
    else:
        return False, None
    if not isinstance(arguments, dict) or arguments.get("value") != _TOOL_VALUE:
        return False, None
    return True, call


def inspect_chat_response(
    response: httpx.Response,
    *,
    expect_tool: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": response.status_code,
        "compatible": False,
    }
    if response.status_code >= 400:
        result["failure"] = "http_status"
        return result
    try:
        data = response.json()
    except ValueError:
        result["failure"] = "invalid_json"
        result["body_prefix"] = response.text[:160]
        return result
    if not isinstance(data, dict):
        result["failure"] = "non_object_response"
        return result

    result["top_level_keys"] = sorted(data.keys())
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        result["failure"] = "missing_top_level_choices"
        return result
    choice = choices[0]
    if not isinstance(choice, dict):
        result["failure"] = "invalid_choice"
        return result
    message = choice.get("message")
    if not isinstance(message, dict):
        result["failure"] = "invalid_message"
        return result

    result["finish_reason"] = choice.get("finish_reason")
    result["message_keys"] = sorted(message.keys())
    for key in ("reasoning", "reasoning_content", "reasoning_details"):
        if key in message:
            result.setdefault("reasoning_fields", []).append(key)

    content = _normalize_content(message.get("content"))
    if content:
        result["content_preview"] = content[:240]
    if _has_internal_markup(content):
        result["failure"] = "internal_tool_markup_leak"
        return result

    calls = message.get("tool_calls")
    if calls is not None and not isinstance(calls, list):
        result["failure"] = "invalid_tool_call"
        return result
    if isinstance(calls, list):
        result["tool_call_count"] = len(calls)

    if expect_tool:
        valid, call = _parse_tool_call(message)
        if not valid or call is None:
            result["failure"] = "invalid_tool_call"
            return result
        result["tool_call_id"] = call["id"]
        result["compatible"] = True
        return result

    if isinstance(calls, list) and calls:
        result["failure"] = "unexpected_tool_call"
        return result
    if not content.strip():
        result["failure"] = "empty_content"
        return result

    usage = data.get("usage")
    if isinstance(usage, dict):
        result["usage"] = {
            key: usage.get(key)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if usage.get(key) is not None
        }
    result["compatible"] = True
    return result


def inspect_continuation_response(
    response: httpx.Response,
    *,
    marker: str = TOOL_CONTINUATION_MARKER,
) -> dict[str, Any]:
    result = inspect_chat_response(response, expect_tool=False)
    if not result.get("compatible"):
        return result
    message = _assistant_message(response)
    content = _normalize_content(message.get("content")) if message is not None else ""
    if marker not in content:
        result["compatible"] = False
        result["failure"] = "continuation_marker_missing"
    return result


def inspect_stream_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "event_count": len(events),
        "compatible": False,
    }
    content_parts: list[str] = []
    valid_choice_seen = False
    for event in events:
        if not isinstance(event, dict):
            continue
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        valid_choice_seen = True
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        content = _normalize_content(delta.get("content"))
        if content:
            content_parts.append(content)

    content = "".join(content_parts)
    result["content"] = content
    if not valid_choice_seen:
        result["failure"] = "missing_stream_choices"
        return result
    if _has_internal_markup(content):
        result["failure"] = "internal_tool_markup_leak"
        return result
    if not content.strip():
        result["failure"] = "empty_content"
        return result
    result["compatible"] = True
    return result


def build_tool_continuation(
    initial_payload: dict[str, Any],
    assistant_message: dict[str, Any],
) -> dict[str, Any]:
    valid, call = _parse_tool_call(assistant_message)
    if not valid or call is None:
        raise ValueError("assistant message does not contain a valid echo_probe call")
    payload = {
        "model": initial_payload["model"],
        "messages": deepcopy(initial_payload["messages"])
        + [
            deepcopy(assistant_message),
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": (
                    f"Tool result marker: {TOOL_CONTINUATION_MARKER}. "
                    f"Include {TOOL_CONTINUATION_MARKER} exactly in the final answer."
                ),
            },
        ],
    }
    if "tools" in initial_payload:
        payload["tools"] = deepcopy(initial_payload["tools"])
    return payload


def _response_summary(response: httpx.Response, elapsed_sec: float) -> dict[str, Any]:
    return {
        "status": response.status_code,
        "elapsed_sec": round(elapsed_sec, 4),
    }


def _retry_delay_seconds(
    response: httpx.Response,
    retry_index: int,
    base_delay: float,
) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    backoff = base_delay * (2**retry_index)
    return backoff + random.uniform(0.0, backoff * 0.25)


async def post_chat(
    client: Any,
    payload: dict[str, Any],
    *,
    max_retries: int = 2,
    retry_base_delay: float = 0.5,
) -> tuple[httpx.Response, dict[str, Any]]:
    request_payload = {**payload, "stream": False}
    started = time.perf_counter()
    retries = 0
    while True:
        response = await client.post("chat/completions", json=request_payload)
        if response.status_code != 429 or retries >= max_retries:
            summary = _response_summary(response, time.perf_counter() - started)
            summary["retry_count"] = retries
            return response, summary
        delay = _retry_delay_seconds(response, retries, retry_base_delay)
        retries += 1
        await asyncio.sleep(delay)


async def stream_chat(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    events: list[dict[str, Any]] = []
    first_event_sec: float | None = None
    first_content_sec: float | None = None
    invalid_event_count = 0

    async with client.stream("POST", "chat/completions", json=payload) as response:
        status = response.status_code
        if status >= 400:
            await response.aread()
            return {
                "status": status,
                "compatible": False,
                "failure": "http_status",
                "elapsed_sec": round(time.perf_counter() - started, 4),
            }
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            now = time.perf_counter()
            if first_event_sec is None:
                first_event_sec = now - started
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                invalid_event_count += 1
                continue
            if not isinstance(event, dict):
                invalid_event_count += 1
                continue
            events.append(event)
            if first_content_sec is None:
                choices = event.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    delta = choices[0].get("delta")
                    if isinstance(delta, dict) and _normalize_content(delta.get("content")):
                        first_content_sec = now - started

    result = inspect_stream_events(events)
    result.update(
        {
            "status": status,
            "elapsed_sec": round(time.perf_counter() - started, 4),
            "first_event_sec": (
                round(first_event_sec, 4) if first_event_sec is not None else None
            ),
            "first_content_sec": (
                round(first_content_sec, 4) if first_content_sec is not None else None
            ),
            "invalid_event_count": invalid_event_count,
        }
    )
    if invalid_event_count and result.get("compatible"):
        result["compatible"] = False
        result["failure"] = "invalid_stream_event"
    return result


def _assistant_message(response: httpx.Response) -> dict[str, Any] | None:
    try:
        data = response.json()
        message = data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    return message if isinstance(message, dict) else None


def _record(model: str, probe: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"model": model, "probe": probe, **details}


async def probe_model(
    client: httpx.AsyncClient,
    model: str,
    *,
    include_stream: bool,
    include_tools: bool,
    max_retries: int,
) -> tuple[list[dict[str, Any]], bool]:
    records: list[dict[str, Any]] = []
    production_ok = True

    plain_payload = build_chat_payload(model, mode="plain")
    plain_response, timing = await post_chat(
        client,
        plain_payload,
        max_retries=max_retries,
    )
    plain = inspect_chat_response(plain_response, expect_tool=False)
    plain.update(timing)
    records.append(_record(model, "nonstream_plain", plain))
    production_ok = production_ok and bool(plain.get("compatible"))

    if include_stream:
        stream = await stream_chat(
            client,
            build_chat_payload(model, mode="plain", stream=True),
        )
        stream["diagnostic_only"] = True
        records.append(_record(model, "stream_plain", stream))

    if include_tools:
        no_tool_response, timing = await post_chat(
            client,
            build_chat_payload(model, mode="no_tool"),
            max_retries=max_retries,
        )
        no_tool = inspect_chat_response(no_tool_response, expect_tool=False)
        no_tool.update(timing)
        records.append(_record(model, "no_tool_discipline", no_tool))
        production_ok = production_ok and bool(no_tool.get("compatible"))

        tool_payload = build_chat_payload(model, mode="tool")
        tool_response, timing = await post_chat(
            client,
            tool_payload,
            max_retries=max_retries,
        )
        tool = inspect_chat_response(tool_response, expect_tool=True)
        tool.update(timing)
        records.append(_record(model, "tool_call", tool))
        tool_ok = bool(tool.get("compatible"))
        production_ok = production_ok and tool_ok

        if tool_ok:
            message = _assistant_message(tool_response)
            if message is None:
                continuation = {
                    "status": tool_response.status_code,
                    "compatible": False,
                    "failure": "invalid_message",
                }
            else:
                continuation_payload = build_tool_continuation(tool_payload, message)
                continuation_response, continuation_timing = await post_chat(
                    client,
                    continuation_payload,
                    max_retries=max_retries,
                )
                continuation = inspect_continuation_response(continuation_response)
                continuation.update(continuation_timing)
            records.append(_record(model, "tool_continuation", continuation))
            production_ok = production_ok and bool(continuation.get("compatible"))

    return records, production_ok


async def run(args: argparse.Namespace) -> int:
    api_key = os.environ.get("CHAINNODE_API_KEY", "").strip()
    if not api_key:
        print("probe-chainnode: CHAINNODE_API_KEY is required", file=sys.stderr)
        return 2

    base_url = os.environ.get("CHAINNODE_BASE_URL", DEFAULT_BASE_URL).strip()
    requested = tuple(part.strip() for part in args.models.split(",") if part.strip())
    if not requested:
        print("probe-chainnode: at least one model is required", file=sys.stderr)
        return 2

    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(args.timeout)
    all_ok = True
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/") + "/",
        headers=headers,
        timeout=timeout,
    ) as client:
        started = time.perf_counter()
        models_response = await client.get("models")
        models_elapsed = round(time.perf_counter() - started, 4)
        try:
            catalog_data = models_response.json()
        except ValueError:
            catalog_data = None
        catalog = extract_model_ids(catalog_data)
        catalog_ok = models_response.status_code < 400 and bool(catalog)
        print(
            json.dumps(
                {
                    "probe": "models",
                    "status": models_response.status_code,
                    "elapsed_sec": models_elapsed,
                    "compatible": catalog_ok,
                    "model_count": len(catalog),
                    "requested_found": [model for model in requested if model in catalog],
                    "requested_missing": [model for model in requested if model not in catalog],
                },
                ensure_ascii=False,
            )
        )
        all_ok = all_ok and catalog_ok

        for model in requested:
            if catalog and model not in catalog:
                print(
                    json.dumps(
                        {
                            "model": model,
                            "probe": "catalog_presence",
                            "compatible": False,
                            "failure": "model_not_in_catalog",
                        },
                        ensure_ascii=False,
                    )
                )
                all_ok = False
                continue
            records, model_ok = await probe_model(
                client,
                model,
                include_stream=not args.skip_stream,
                include_tools=not args.skip_tools,
                max_retries=max(0, args.max_retries),
            )
            for record in records:
                print(json.dumps(record, ensure_ascii=False))
            all_ok = all_ok and model_ok

    return 0 if all_ok else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Qualify Chainnode models for OpenAI-compatible bot integration"
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        help="Comma-separated Chainnode model IDs",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--skip-stream", action="store_true")
    parser.add_argument("--skip-tools", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        return asyncio.run(run(parse_args()))
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"probe-chainnode: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
