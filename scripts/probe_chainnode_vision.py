"""Live Chainnode vision qualification probe, isolated from bot runtime.

The probe sends the same OpenAI-compatible ``image_url`` data-URI shape used by
``app.ai.multimodal``. A deterministic PNG contains a code that is deliberately
absent from the text prompt, so a passing response must obtain it from the image.
Non-stream vision is the production gate. Streaming and image+tool behavior are
reported separately as diagnostics/capabilities and do not mutate runtime config.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
import json
import os
import random
import re
import struct
import sys
from typing import Any
import zlib

import httpx


DEFAULT_BASE_URL = "https://dn.chainno.de/v1"
DEFAULT_MODELS = (
    "cl/cline-free/muse-spark-1.3-contributor",
    "cl/z-ai/glm-5.3-flash",
    "cl/cline-free/deepseek-v4.1-flash",
)
EXPECTED_VISION_CODE = "47-GREEN-CIRCLE"
TOOL_CONTINUATION_MARKER = "CHAINNODE_VISION_TOOL_OK"
_INTERNAL_MARKUP_RE = re.compile(
    r"</?(?:tool_call|arg_key|arg_value)>|<\s*/?\s*[|｜]\s*/?dsml[|｜]",
    flags=re.IGNORECASE,
)
_TOOL_UNSUPPORTED_RE = re.compile(
    r"(?:does not support|do not support|not support|unsupported)"
    r"[^.\n]{0,80}(?:tool|function)"
    r"|(?:tool|function)[^.\n]{0,80}(?:not supported|unsupported)",
    flags=re.IGNORECASE,
)
_TOOL = {
    "type": "function",
    "function": {
        "name": "report_vision_probe",
        "description": "Report the exact code read from the supplied image.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    },
}


def _probe_png_bytes() -> bytes:
    width, height = 640, 320
    pixels = bytearray([255, 255, 255] * width * height)

    def set_pixel(x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 3
            pixels[offset : offset + 3] = bytes(rgb)

    def rect(x0: int, y0: int, x1: int, y1: int, rgb: tuple[int, int, int]) -> None:
        for y in range(y0, y1):
            for x in range(x0, x1):
                set_pixel(x, y, rgb)

    def circle(cx: int, cy: int, radius: int, rgb: tuple[int, int, int]) -> None:
        radius_sq = radius * radius
        for y in range(cy - radius, cy + radius + 1):
            for x in range(cx - radius, cx + radius + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius_sq:
                    set_pixel(x, y, rgb)

    digit_segments = {
        "4": {"b", "c", "f", "g"},
        "7": {"a", "b", "c"},
    }
    segment_rects = {
        "a": (18, 0, 92, 14),
        "b": (92, 12, 106, 88),
        "c": (92, 92, 106, 168),
        "d": (18, 166, 92, 180),
        "e": (4, 92, 18, 168),
        "f": (4, 12, 18, 88),
        "g": (18, 83, 92, 97),
    }
    for digit, origin_x in (("4", 80), ("7", 220)):
        for segment in digit_segments[digit]:
            x0, y0, x1, y1 = segment_rects[segment]
            rect(origin_x + x0, 70 + y0, origin_x + x1, 70 + y1, (0, 0, 0))

    circle(500, 160, 70, (20, 170, 70))

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        start = y * stride
        raw.extend(pixels[start : start + stride])

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        checksum = zlib.crc32(body) & 0xFFFFFFFF
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", checksum)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
        + chunk(b"IEND", b"")
    )


def _image_data_url() -> str:
    encoded = base64.b64encode(_probe_png_bytes()).decode("ascii")
    return "data:image/png;base64," + encoded


def build_vision_payload(
    model: str,
    *,
    mode: str = "vision",
    stream: bool = False,
) -> dict[str, Any]:
    if mode == "vision":
        prompt = (
            "The image contains a black two-digit seven-segment number and one "
            "colored geometric shape. Reply exactly as NUMBER-COLOR-SHAPE using "
            "uppercase English words for color and shape."
        )
        include_tools = False
    elif mode == "tool":
        prompt = (
            "Inspect the black two-digit seven-segment number and colored "
            "geometric shape in the image. Build NUMBER-COLOR-SHAPE in uppercase, "
            "then call report_vision_probe with that code. Do not answer directly first."
        )
        include_tools = True
    else:
        raise ValueError(f"unsupported vision probe mode: {mode}")

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an API vision compatibility probe."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_data_url()}},
                ],
            },
        ],
    }
    if include_tools:
        payload["tools"] = [_TOOL]
    if stream:
        payload["stream"] = True
    return payload


def extract_model_ids(data: object) -> set[str]:
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return set()
    return {
        str(item.get("id")).strip()
        for item in data["data"]
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }


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


def _safe_message(
    response: httpx.Response,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    result: dict[str, Any] = {"status": response.status_code, "compatible": False}
    if response.status_code >= 400:
        result["failure"] = "http_status"
        return None, result
    try:
        data = response.json()
    except ValueError:
        result["failure"] = "invalid_json"
        return None, result
    if not isinstance(data, dict):
        result["failure"] = "non_object_response"
        return None, result
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        result["failure"] = "missing_top_level_choices"
        return None, result
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        result["failure"] = "invalid_message"
        return None, result
    result["finish_reason"] = choice.get("finish_reason")
    return choice["message"], result


def inspect_vision_response(response: httpx.Response) -> dict[str, Any]:
    message, result = _safe_message(response)
    if message is None:
        return result
    content = _normalize_content(message.get("content")).strip()
    result["content_preview"] = content[:240]
    if _INTERNAL_MARKUP_RE.search(content):
        result["failure"] = "internal_tool_markup_leak"
        return result
    calls = message.get("tool_calls")
    if isinstance(calls, list) and calls:
        result["failure"] = "unexpected_tool_call"
        return result
    if content != EXPECTED_VISION_CODE:
        result["failure"] = "vision_code_mismatch"
        return result
    result["compatible"] = True
    return result


def _tool_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        return str(data["error"].get("message") or "")
    return response.text


def _parse_tool_call(message: dict[str, Any]) -> dict[str, Any] | None:
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        return None
    call = calls[0]
    call_id = str(call.get("id") or "").strip()
    fn = call.get("function")
    if (
        not call_id
        or call.get("type") not in (None, "function")
        or not isinstance(fn, dict)
    ):
        return None
    if fn.get("name") != "report_vision_probe":
        return None
    raw_args = fn.get("arguments")
    if isinstance(raw_args, dict):
        args = raw_args
    elif isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            return None
    else:
        return None
    if not isinstance(args, dict) or args.get("code") != EXPECTED_VISION_CODE:
        return None
    return call


def inspect_tool_response(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        message = _tool_error_message(response)
        if _TOOL_UNSUPPORTED_RE.search(message):
            return {
                "status": response.status_code,
                "compatible": False,
                "supported": False,
                "failure": "tools_unsupported",
            }
        return {
            "status": response.status_code,
            "compatible": False,
            "supported": None,
            "failure": "http_status",
        }

    message, result = _safe_message(response)
    result["supported"] = True
    if message is None:
        return result
    content = _normalize_content(message.get("content"))
    if _INTERNAL_MARKUP_RE.search(content):
        result["failure"] = "internal_tool_markup_leak"
        return result
    call = _parse_tool_call(message)
    if call is None:
        result["failure"] = "invalid_tool_call"
        return result
    result["tool_call_id"] = call["id"]
    result["compatible"] = True
    return result


def build_tool_continuation(
    initial_payload: dict[str, Any],
    assistant_message: dict[str, Any],
) -> dict[str, Any]:
    call = _parse_tool_call(assistant_message)
    if call is None:
        raise ValueError("assistant message does not contain a valid vision probe call")
    payload = {
        "model": initial_payload["model"],
        "messages": deepcopy(initial_payload["messages"])
        + [
            deepcopy(assistant_message),
            {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": (
                    f"Accepted code {EXPECTED_VISION_CODE}. Final answer must include "
                    f"{TOOL_CONTINUATION_MARKER} exactly."
                ),
            },
        ],
    }
    if "tools" in initial_payload:
        payload["tools"] = deepcopy(initial_payload["tools"])
    return payload


def inspect_continuation_response(response: httpx.Response) -> dict[str, Any]:
    message, result = _safe_message(response)
    if message is None:
        return result
    content = _normalize_content(message.get("content"))
    result["content_preview"] = content[:240]
    if _INTERNAL_MARKUP_RE.search(content):
        result["failure"] = "internal_tool_markup_leak"
        return result
    if TOOL_CONTINUATION_MARKER not in content:
        result["failure"] = "continuation_marker_missing"
        return result
    result["compatible"] = True
    return result


def inspect_stream_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    parts: list[str] = []
    valid_choice_seen = False
    for event in events:
        choices = event.get("choices") if isinstance(event, dict) else None
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            continue
        valid_choice_seen = True
        delta = choices[0].get("delta")
        if isinstance(delta, dict):
            content = _normalize_content(delta.get("content"))
            if content:
                parts.append(content)
    content = "".join(parts).strip()
    result: dict[str, Any] = {
        "event_count": len(events),
        "content": content,
        "compatible": False,
    }
    if not valid_choice_seen:
        result["failure"] = "missing_stream_choices"
    elif _INTERNAL_MARKUP_RE.search(content):
        result["failure"] = "internal_tool_markup_leak"
    elif content != EXPECTED_VISION_CODE:
        result["failure"] = "vision_code_mismatch"
    else:
        result["compatible"] = True
    return result


def _retry_delay(response: httpx.Response, retry_index: int) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    base = 0.5 * (2**retry_index)
    return base + random.uniform(0.0, base * 0.25)


async def post_chat(
    client: Any,
    payload: dict[str, Any],
    *,
    max_retries: int = 2,
) -> httpx.Response:
    request_payload = {**payload, "stream": False}
    retries = 0
    while True:
        response = await client.post("chat/completions", json=request_payload)
        if response.status_code != 429 or retries >= max_retries:
            return response
        await asyncio.sleep(_retry_delay(response, retries))
        retries += 1


async def stream_chat(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    invalid_event_count = 0
    request_payload = {**payload, "stream": True}
    async with client.stream(
        "POST",
        "chat/completions",
        json=request_payload,
    ) as response:
        if response.status_code >= 400:
            await response.aread()
            return {
                "status": response.status_code,
                "compatible": False,
                "failure": "http_status",
            }
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                invalid_event_count += 1
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                invalid_event_count += 1
    result = inspect_stream_events(events)
    result["status"] = response.status_code
    result["invalid_event_count"] = invalid_event_count
    if invalid_event_count and result.get("compatible"):
        result["compatible"] = False
        result["failure"] = "invalid_stream_event"
    return result


def _assistant_message(response: httpx.Response) -> dict[str, Any] | None:
    message, _ = _safe_message(response)
    return message


async def probe_model(
    client: httpx.AsyncClient,
    model: str,
    *,
    include_stream: bool,
    include_tools: bool,
    max_retries: int,
) -> tuple[list[dict[str, Any]], bool]:
    records: list[dict[str, Any]] = []

    vision_response = await post_chat(
        client,
        build_vision_payload(model),
        max_retries=max_retries,
    )
    vision = inspect_vision_response(vision_response)
    records.append({"model": model, "probe": "nonstream_vision", **vision})
    vision_ok = bool(vision.get("compatible"))

    if include_stream:
        stream = await stream_chat(client, build_vision_payload(model, stream=True))
        records.append(
            {
                "model": model,
                "probe": "stream_vision",
                "diagnostic_only": True,
                **stream,
            }
        )

    if include_tools:
        tool_payload = build_vision_payload(model, mode="tool")
        tool_response = await post_chat(client, tool_payload, max_retries=max_retries)
        tool = inspect_tool_response(tool_response)
        records.append(
            {
                "model": model,
                "probe": "vision_tool_call",
                "diagnostic_only": True,
                **tool,
            }
        )
        if tool.get("compatible"):
            assistant = _assistant_message(tool_response)
            if assistant is not None:
                continuation_response = await post_chat(
                    client,
                    build_tool_continuation(tool_payload, assistant),
                    max_retries=max_retries,
                )
                continuation = inspect_continuation_response(continuation_response)
                records.append(
                    {
                        "model": model,
                        "probe": "vision_tool_continuation",
                        "diagnostic_only": True,
                        **continuation,
                    }
                )

    return records, vision_ok


async def run(args: argparse.Namespace) -> int:
    api_key = os.environ.get("CHAINNODE_API_KEY", "").strip()
    if not api_key:
        print("probe-chainnode-vision: CHAINNODE_API_KEY is required", file=sys.stderr)
        return 2

    requested = tuple(part.strip() for part in args.models.split(",") if part.strip())
    if not requested:
        print("probe-chainnode-vision: at least one model is required", file=sys.stderr)
        return 2

    base_url = os.environ.get("CHAINNODE_BASE_URL", DEFAULT_BASE_URL).strip()
    headers = {"Authorization": f"Bearer {api_key}"}
    all_ok = True
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/") + "/",
        headers=headers,
        timeout=httpx.Timeout(args.timeout),
    ) as client:
        models_response = await client.get("models")
        try:
            catalog = extract_model_ids(models_response.json())
        except ValueError:
            catalog = set()
        catalog_ok = models_response.status_code < 400 and bool(catalog)
        print(
            json.dumps(
                {
                    "probe": "models",
                    "status": models_response.status_code,
                    "compatible": catalog_ok,
                    "requested_found": [m for m in requested if m in catalog],
                    "requested_missing": [m for m in requested if m not in catalog],
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
        description="Qualify Chainnode models for OpenAI-compatible image input"
    )
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--skip-stream", action="store_true")
    parser.add_argument("--skip-tools", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        return asyncio.run(run(parse_args()))
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"probe-chainnode-vision: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
