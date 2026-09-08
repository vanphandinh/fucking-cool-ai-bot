"""Manual live contract probe for B.AI's promoted zero-credit chat models.

This script is intentionally separate from runtime. Baseline requests use only
fields documented by B.AI's OpenAI-compatible Chat Completions endpoint.
Provider-specific reasoning knobs are tested only behind
``--experimental-reasoning`` and their failures do not affect the baseline
exit status.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ai.bai import SUPPORTED_PROMO_MODELS, model_supports_vision  # noqa: E402

_BASE_URL = "https://api.b.ai/v1"
_TOOL = {
    "type": "function",
    "function": {
        "name": "echo_probe",
        "description": "Return a probe value unchanged.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
}


def build_chat_payload(
    model: str,
    *,
    include_tool: bool = False,
    image_data_url: str | None = None,
) -> dict[str, Any]:
    user_content: str | list[dict[str, Any]] = "Reply with exactly: BAI_PROBE_OK"
    if image_data_url is not None:
        user_content = [
            {"type": "text", "text": "Describe this image in one short sentence."},
            {"type": "image_url", "image_url": {"url": image_data_url}},
        ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a compatibility probe."},
            {"role": "user", "content": user_content},
        ],
    }
    if include_tool:
        payload["tools"] = [_TOOL]
    return payload


def experimental_reasoning_overrides() -> dict[str, dict[str, Any]]:
    """Return upstream-style knobs that must never be used by runtime blindly."""
    return {
        "qwen3.8-flash": {"enable_thinking": False},
        "mimo-v2.5": {"thinking": {"type": "disabled"}},
        "glm-5.3-flash": {"reasoning_effort": "low"},
    }


def _image_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("probe image must be JPEG, PNG, or WebP")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _response_summary(response: httpx.Response, elapsed_sec: float) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": response.status_code,
        "elapsed_sec": round(elapsed_sec, 3),
    }
    try:
        data = response.json()
    except ValueError:
        summary["json"] = False
        summary["body_prefix"] = response.text[:160]
        return summary

    summary["json"] = True
    if isinstance(data, dict):
        summary["top_level_keys"] = sorted(data.keys())
        choices = data.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
            summary["finish_reason"] = choice.get("finish_reason")
            message = choice.get("message")
            if isinstance(message, dict):
                summary["message_keys"] = sorted(message.keys())
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    summary["tool_call_count"] = len(tool_calls)
        error = data.get("error")
        if isinstance(error, dict):
            summary["error_code"] = error.get("code")
            summary["error_type"] = error.get("type")
            summary["error_message"] = str(error.get("message") or "")[:240]
    return summary


async def _post_chat(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> tuple[httpx.Response, dict[str, Any]]:
    started = time.perf_counter()
    response = await client.post("chat/completions", json=payload)
    return response, _response_summary(response, time.perf_counter() - started)


async def _probe_one(
    client: httpx.AsyncClient,
    model: str,
    *,
    tools: bool,
    image_data_url: str | None,
    experimental_reasoning: bool,
) -> tuple[list[dict[str, Any]], bool]:
    records: list[dict[str, Any]] = []
    baseline = build_chat_payload(
        model,
        include_tool=tools,
        image_data_url=image_data_url if model_supports_vision(model) else None,
    )
    response, summary = await _post_chat(client, baseline)
    records.append({"model": model, "probe": "baseline", **summary})
    baseline_ok = response.status_code < 400

    if tools and baseline_ok:
        try:
            data = response.json()
            message = data["choices"][0]["message"]
            calls = message.get("tool_calls") or []
        except (KeyError, IndexError, TypeError, ValueError):
            calls = []
            message = None
        if isinstance(message, dict) and isinstance(calls, list) and calls:
            call_id = str(calls[0].get("id") or "")
            if call_id:
                continuation = {
                    "model": model,
                    "messages": baseline["messages"]
                    + [
                        message,
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": "BAI_TOOL_PROBE_OK",
                        },
                    ],
                    "tools": [_TOOL],
                }
                followup, followup_summary = await _post_chat(client, continuation)
                records.append(
                    {"model": model, "probe": "tool_continuation", **followup_summary}
                )
                baseline_ok = baseline_ok and followup.status_code < 400

    if experimental_reasoning:
        override = experimental_reasoning_overrides().get(model)
        if override is not None:
            experimental = build_chat_payload(model)
            experimental.update(override)
            _, experimental_summary = await _post_chat(client, experimental)
            records.append(
                {
                    "model": model,
                    "probe": "experimental_reasoning",
                    "override_keys": sorted(override.keys()),
                    **experimental_summary,
                }
            )

    return records, baseline_ok


async def _run(args: argparse.Namespace) -> int:
    api_key = os.environ.get("BAI_API_KEY", "").strip()
    if not api_key:
        print("probe-bai: BAI_API_KEY is required", file=sys.stderr)
        return 2

    requested = [part.strip() for part in args.models.split(",") if part.strip()]
    unsupported = [model for model in requested if model not in SUPPORTED_PROMO_MODELS]
    if unsupported:
        print(
            "probe-bai: unsupported promoted model(s): " + ", ".join(unsupported),
            file=sys.stderr,
        )
        return 2

    image_data_url = _image_data_url(Path(args.image)) if args.image else None
    headers = {"Authorization": f"Bearer {api_key}"}
    timeout = httpx.Timeout(args.timeout)
    baseline_ok = True

    async with httpx.AsyncClient(base_url=_BASE_URL + "/", headers=headers, timeout=timeout) as client:
        started = time.perf_counter()
        models_response = await client.get("models")
        model_summary = _response_summary(models_response, time.perf_counter() - started)
        print(json.dumps({"probe": "models", **model_summary}, ensure_ascii=False))
        if models_response.status_code >= 400:
            baseline_ok = False

        for model in requested:
            records, model_ok = await _probe_one(
                client,
                model,
                tools=args.tools,
                image_data_url=image_data_url,
                experimental_reasoning=args.experimental_reasoning,
            )
            for record in records:
                print(json.dumps(record, ensure_ascii=False))
            baseline_ok = baseline_ok and model_ok

    return 0 if baseline_ok else 2


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe B.AI Chat Completions compatibility")
    parser.add_argument(
        "--models",
        default=",".join(sorted(SUPPORTED_PROMO_MODELS)),
        help="Comma-separated promoted models to probe",
    )
    parser.add_argument("--tools", action="store_true", help="Probe function/tool calling")
    parser.add_argument("--image", help="JPEG/PNG/WebP path for multimodal probe")
    parser.add_argument(
        "--experimental-reasoning",
        action="store_true",
        help="Try unverified upstream reasoning knobs; failures are informational",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    try:
        return asyncio.run(_run(_parse_args()))
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"probe-bai: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
