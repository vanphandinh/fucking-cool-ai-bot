from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def _bounded_redacted(value: object, *, secret: str, limit: int = 500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if secret:
        text = text.replace(secret, "[redacted]")
    return text[:limit]


def _extract_chat_text(data: dict) -> str:
    value = data["choices"][0]["message"]["content"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Aurora chat response has no text content")
    return value.strip()


def _extract_tool_call(data: dict) -> dict:
    call = data["choices"][0]["message"]["tool_calls"][0]
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict) or function.get("name") != "echo_probe":
        raise ValueError("Aurora did not return echo_probe")
    arguments = json.loads(function.get("arguments") or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("Aurora tool arguments are not an object")
    return call


def _config() -> tuple[str, str, str]:
    base_url = os.getenv("AURORA_BASE_URL", "http://aurora:8080/v1").rstrip("/")
    api_key = os.getenv("AURORA_API_KEY", "").strip()
    model = os.getenv("AURORA_MODEL", "auto").strip() or "auto"
    if not api_key:
        raise ValueError("AURORA_API_KEY is required")
    return base_url, api_key, model


def _probe_models(client: httpx.Client) -> None:
    response = client.get("models")
    response.raise_for_status()
    data = response.json()
    models = data.get("data") if isinstance(data, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("Aurora /v1/models returned no models")
    print(f"models: ok ({len(models)})")


def _probe_chat(client: httpx.Client, model: str, api_key: str) -> None:
    response = client.post(
        "chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: aurora-ok"}],
        },
    )
    response.raise_for_status()
    print("chat:", _bounded_redacted(_extract_chat_text(response.json()), secret=api_key))


def _probe_tool(client: httpx.Client, model: str) -> None:
    response = client.post(
        "chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Call echo_probe with value aurora."}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "echo_probe",
                        "description": "Smoke-test function. Do not answer in prose.",
                        "parameters": {
                            "type": "object",
                            "properties": {"value": {"type": "string"}},
                            "required": ["value"],
                        },
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": "echo_probe"}},
        },
    )
    response.raise_for_status()
    call = _extract_tool_call(response.json())
    print("tool: ok", str(call.get("id") or ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("models", "chat", "tool", "all"), default="all")
    args = parser.parse_args()
    api_key = ""
    try:
        base_url, api_key, model = _config()
        with httpx.Client(
            base_url=base_url + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        ) as client:
            if args.mode in ("models", "all"):
                _probe_models(client)
            if args.mode in ("chat", "all"):
                _probe_chat(client, model, api_key)
            if args.mode in ("tool", "all"):
                _probe_tool(client, model)
        return 0
    except (httpx.HTTPError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        print(
            "aurora probe failed:",
            _bounded_redacted(str(exc), secret=api_key),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
