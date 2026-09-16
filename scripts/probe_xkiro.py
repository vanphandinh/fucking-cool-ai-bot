#!/usr/bin/env python3
"""Live xKiro catalog and OpenAI-compatible qualification probe.

The probe intentionally keeps model IDs out of source code. Operators supply
candidate IDs, while GET /models remains the source of truth for free-tier,
pricing, tools, and vision capability gates.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from decimal import Decimal, InvalidOperation
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
from typing import Any

import httpx

BASE_URL = "https://api.xkiro.com/v1"
DEFAULT_VISION_EXPECT = "47-GREEN-CIRCLE"
DEFAULT_MAX_RETRY_AFTER = 60.0
TOOL_RESULT = "XKIRO_TOOL_PROBE_OK"
TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "echo_probe",
        "description": "Return the supplied probe value unchanged.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    },
}


def emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, ensure_ascii=False, sort_keys=True))


def find_model(catalog: dict, model_id: str) -> dict | None:
    data = catalog.get("data")
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict) and item.get("id") == model_id:
            return item
    return None


def _is_zero_price(value: object) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return Decimal(str(value)) == Decimal("0")
    except (InvalidOperation, ValueError):
        return False


def qualification_errors(model: dict, *, require_vision: bool) -> list[str]:
    errors: list[str] = []
    if model.get("access_tier") != "free":
        errors.append("access_tier must be free")

    pricing = model.get("pricing")
    if not isinstance(pricing, dict):
        errors.append("pricing object is missing")
    else:
        if not _is_zero_price(pricing.get("input")):
            errors.append("pricing.input must be zero")
        if not _is_zero_price(pricing.get("output")):
            errors.append("pricing.output must be zero")

    capabilities = model.get("capabilities")
    if not isinstance(capabilities, dict):
        errors.append("capabilities object is missing")
    else:
        if capabilities.get("tools") is not True:
            errors.append("capabilities.tools must be true")
        if require_vision and capabilities.get("vision") is not True:
            errors.append("capabilities.vision must be true")
    return errors


def build_chat_payload(
    model: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
    return payload


def response_message(data: dict) -> dict:
    try:
        choices = data["choices"]
        message = choices[0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("malformed response: missing choices[0].message") from exc
    if not isinstance(message, dict):
        raise ValueError("malformed response: message must be an object")
    return message


def response_content(data: dict) -> str:
    message = response_message(data)
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        if "".join(parts).strip():
            return "".join(parts).strip()
    raise ValueError("malformed response: assistant content is empty")


def extract_tool_call(data: dict) -> dict:
    message = response_message(data)
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        raise ValueError("structured tool call is missing")
    call = calls[0]
    if not isinstance(call, dict):
        raise ValueError("structured tool call must be an object")
    call_id = str(call.get("id") or "").strip()
    function = call.get("function")
    if not call_id or not isinstance(function, dict):
        raise ValueError("structured tool call is missing id/function")
    if call.get("type") not in (None, "function"):
        raise ValueError("structured tool call type must be function")
    if str(function.get("name") or "").strip() != "echo_probe":
        raise ValueError("structured tool call did not call echo_probe")
    raw_arguments = function.get("arguments")
    if isinstance(raw_arguments, str):
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ValueError("structured tool call arguments are not JSON") from exc
    elif isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        raise ValueError("structured tool call arguments are missing")
    if not isinstance(arguments, dict):
        raise ValueError("structured tool call arguments must be an object")
    if arguments.get("value") != "XKIRO_TOOL_PROBE":
        raise ValueError("structured tool call returned the wrong probe value")
    return call


def build_tool_continuation(
    model: str,
    base_messages: list[dict],
    assistant_message: dict,
    tool_call: dict,
    *,
    tool_result: str = TOOL_RESULT,
) -> dict:
    call_id = str(tool_call.get("id") or "").strip()
    if not call_id:
        raise ValueError("structured tool call is missing id")
    messages = [
        *base_messages,
        assistant_message,
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": tool_result,
        },
    ]
    return build_chat_payload(model, messages, tools=[TOOL_SCHEMA])


def image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def vision_message(data_url: str) -> dict:
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    "Read the unique verification code visible in this image and "
                    "reply with that code exactly. Do not guess."
                ),
            },
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }


def vision_tool_message(data_url: str) -> dict:
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    "Inspect the image, then call echo_probe exactly once with "
                    'value="XKIRO_TOOL_PROBE".'
                ),
            },
            {"type": "image_url", "image_url": {"url": data_url}},
        ],
    }


def _retry_delay(
    response: httpx.Response,
    fallback: float,
    *,
    max_retry_after: float,
) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            parsed = float(raw)
        except ValueError:
            parsed = None
        if parsed is not None and parsed >= 0:
            if parsed > max_retry_after:
                raise ValueError(
                    f"Retry-After {parsed:g}s exceeds max wait "
                    f"{max_retry_after:g}s"
                )
            return parsed
    return min(max(0.0, fallback), max_retry_after)


async def post_chat(
    client: httpx.AsyncClient,
    payload: dict,
    *,
    max_retries: int,
    retry_base_delay: float,
    max_retry_after: float = DEFAULT_MAX_RETRY_AFTER,
) -> tuple[httpx.Response, dict]:
    retry_count = 0
    while True:
        response = await client.post("chat/completions", json=payload)
        if response.status_code != 429 or retry_count >= max_retries:
            return response, {
                "retry_count": retry_count,
                "status_code": response.status_code,
            }
        delay = _retry_delay(
            response,
            retry_base_delay * (2**retry_count),
            max_retry_after=max_retry_after,
        )
        retry_count += 1
        await asyncio.sleep(delay)


async def fetch_catalog(client: httpx.AsyncClient) -> dict:
    response = await client.get("models")
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError("/models response is not JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("/models response is missing data array")
    return data


def catalog_summary(model: dict) -> dict:
    pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
    caps = (
        model.get("capabilities")
        if isinstance(model.get("capabilities"), dict)
        else {}
    )
    return {
        "id": model.get("id"),
        "access_tier": model.get("access_tier"),
        "pricing": {
            "input": pricing.get("input"),
            "output": pricing.get("output"),
        },
        "capabilities": {
            "tools": caps.get("tools"),
            "vision": caps.get("vision"),
        },
        "context_length": model.get("context_length"),
    }


def _json_response(response: httpx.Response, label: str) -> dict:
    if response.status_code >= 400:
        raise ValueError(f"{label} HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError(f"{label} response is not JSON") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} response is not an object")
    return data


async def _probe_plain(
    client: httpx.AsyncClient,
    model: str,
    *,
    max_retries: int,
    retry_base_delay: float,
    max_retry_after: float,
    messages: list[dict] | None = None,
    event: str = "plain_chat",
    expected_content: str | None = None,
) -> None:
    messages = messages or [{"role": "user", "content": "Reply with a short OK."}]
    response, retry = await post_chat(
        client,
        build_chat_payload(model, messages),
        max_retries=max_retries,
        retry_base_delay=retry_base_delay,
        max_retry_after=max_retry_after,
    )
    data = _json_response(response, event)
    content = response_content(data)
    if expected_content is not None:
        expected = expected_content.strip().casefold()
        if not expected or expected not in content.casefold():
            raise ValueError(f"{event} response did not contain expected image marker")
    emit(
        event,
        model=model,
        compatible=True,
        retry_count=retry["retry_count"],
        content_preview=content[:120],
    )


async def _probe_tool_flow(
    client: httpx.AsyncClient,
    model: str,
    *,
    max_retries: int,
    retry_base_delay: float,
    max_retry_after: float,
    user_message: dict | None = None,
    event_prefix: str = "tool",
) -> None:
    base_messages = [
        {
            "role": "system",
            "content": "Use the requested function exactly when instructed.",
        },
        user_message
        or {
            "role": "user",
            "content": (
                "Call echo_probe exactly once with "
                'value="XKIRO_TOOL_PROBE". Do not answer directly.'
            ),
        },
    ]
    response, retry = await post_chat(
        client,
        build_chat_payload(model, base_messages, tools=[TOOL_SCHEMA]),
        max_retries=max_retries,
        retry_base_delay=retry_base_delay,
        max_retry_after=max_retry_after,
    )
    first_data = _json_response(response, f"{event_prefix}_call")
    assistant = response_message(first_data)
    tool_call = extract_tool_call(first_data)
    emit(
        f"{event_prefix}_call",
        model=model,
        compatible=True,
        retry_count=retry["retry_count"],
        tool_name=tool_call["function"]["name"],
    )

    marker = f"XKIRO_TOOL_RESULT_{secrets.token_hex(8)}"
    tool_result = (
        f"{marker}\n"
        "Reply with the XKIRO_TOOL_RESULT marker exactly and no other text."
    )
    continuation, continuation_retry = await post_chat(
        client,
        build_tool_continuation(
            model,
            base_messages,
            assistant,
            tool_call,
            tool_result=tool_result,
        ),
        max_retries=max_retries,
        retry_base_delay=retry_base_delay,
        max_retry_after=max_retry_after,
    )
    continuation_data = _json_response(
        continuation,
        f"{event_prefix}_continuation",
    )
    content = response_content(continuation_data)
    if content != marker:
        raise ValueError(
            f"{event_prefix}_continuation response did not reproduce tool result marker"
        )
    emit(
        f"{event_prefix}_continuation",
        model=model,
        compatible=True,
        retry_count=continuation_retry["retry_count"],
        content_preview=content[:120],
    )


async def run_probe(args: argparse.Namespace) -> int:
    api_key = os.getenv("XKIRO_API_KEY", "").strip()
    if not api_key:
        emit("error", compatible=False, error="XKIRO_API_KEY is required")
        return 2

    if args.vision_model and not args.image:
        emit(
            "error",
            compatible=False,
            error="--image is required when --vision-model is supplied",
        )
        return 2

    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(args.timeout)
    async with httpx.AsyncClient(
        base_url=BASE_URL.rstrip("/") + "/",
        headers=headers,
        timeout=timeout,
    ) as client:
        try:
            catalog = await fetch_catalog(client)
            emit("catalog", compatible=True, model_count=len(catalog["data"]))

            requested = [("text", args.text_model, False)]
            if args.vision_model:
                requested.append(("vision", args.vision_model, True))

            for route, model_id, require_vision in requested:
                model = find_model(catalog, model_id)
                if model is None:
                    emit(
                        "catalog_gate",
                        route=route,
                        model=model_id,
                        compatible=False,
                        errors=["model does not exist in live /models catalog"],
                    )
                    return 1
                errors = qualification_errors(model, require_vision=require_vision)
                emit(
                    "catalog_gate",
                    route=route,
                    model=model_id,
                    compatible=not errors,
                    metadata=catalog_summary(model),
                    errors=errors,
                )
                if errors:
                    return 1

            await _probe_plain(
                client,
                args.text_model,
                max_retries=args.max_retries,
                retry_base_delay=args.retry_base_delay,
                max_retry_after=args.max_retry_after,
            )
            await _probe_tool_flow(
                client,
                args.text_model,
                max_retries=args.max_retries,
                retry_base_delay=args.retry_base_delay,
                max_retry_after=args.max_retry_after,
            )

            if args.vision_model:
                data_url = image_data_url(Path(args.image))
                vision = vision_message(data_url)
                await _probe_plain(
                    client,
                    args.vision_model,
                    max_retries=args.max_retries,
                    retry_base_delay=args.retry_base_delay,
                    max_retry_after=args.max_retry_after,
                    messages=[vision],
                    event="vision_chat",
                    expected_content=args.vision_expect,
                )
                await _probe_tool_flow(
                    client,
                    args.vision_model,
                    max_retries=args.max_retries,
                    retry_base_delay=args.retry_base_delay,
                    max_retry_after=args.max_retry_after,
                    user_message=vision_tool_message(data_url),
                    event_prefix="vision_tool",
                )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            emit("error", compatible=False, error=str(exc))
            return 1

    emit(
        "complete",
        compatible=True,
        text_model=args.text_model,
        vision_model=args.vision_model or None,
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-model", required=True)
    parser.add_argument("--vision-model")
    parser.add_argument("--image")
    parser.add_argument(
        "--vision-expect",
        default=DEFAULT_VISION_EXPECT,
        help=(
            "Expected visible verification marker for the known vision fixture "
            f"(default: {DEFAULT_VISION_EXPECT})"
        ),
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--retry-base-delay", type=float, default=0.5)
    parser.add_argument(
        "--max-retry-after",
        type=float,
        default=DEFAULT_MAX_RETRY_AFTER,
        help=(
            "Maximum numeric Retry-After wait in seconds before qualification "
            f"fails closed (default: {DEFAULT_MAX_RETRY_AFTER:g})"
        ),
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be > 0")
    if args.max_retries < 0:
        parser.error("--max-retries must be >= 0")
    if args.retry_base_delay < 0:
        parser.error("--retry-base-delay must be >= 0")
    if args.max_retry_after < 0:
        parser.error("--max-retry-after must be >= 0")
    return args


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run_probe(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))