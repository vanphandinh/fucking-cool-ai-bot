"""Validate the local production SearXNG settings against repo invariants."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_PATH = Path("searxng/settings.yml")
_PLACEHOLDER_SECRET = "REPLACE_WITH_openssl_rand_-hex_32"
_REQUIRED_REMOVED_ENGINES = {
    "ahmia",
    "torch",
    "startpage",
    "startpage news",
    "startpage images",
    "duckduckgo",
    "wikipedia",
    "wikidata",
}


def _removed_engines(text: str) -> set[str]:
    match = re.search(
        r"(?ms)^use_default_settings:\s*\n(?:^[ \t].*\n)*?"
        r"^[ \t]+remove:\s*\n(?P<body>(?:^[ \t]+(?:#.*|- .*)\n?)*)",
        text,
    )
    if not match:
        return set()
    removed: set[str] = set()
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            removed.add(stripped[2:].strip())
    return removed


def validate_settings_text(text: str) -> list[str]:
    errors: list[str] = []

    removed = _removed_engines(text)
    missing = sorted(_REQUIRED_REMOVED_ENGINES - removed)
    if missing:
        errors.append("missing removed engines: " + ", ".join(missing))

    required_literals = {
        "SearxEngineTooManyRequests": "SearxEngineTooManyRequests: 3600",
        "SearxEngineAccessDenied": "SearxEngineAccessDenied: 86400",
        "SearxEngineCaptcha": "SearxEngineCaptcha: 86400",
        "request_timeout": "request_timeout: 3.0",
        "max_request_timeout": "max_request_timeout: 5.0",
        "JSON format": "- json",
        "private limiter": "limiter: false",
        "private instance": "public_instance: false",
    }
    for label, expected in required_literals.items():
        if expected not in text:
            errors.append(f"{label} invariant missing or stale; expected `{expected}`")

    secret_match = re.search(r'(?m)^\s*secret_key:\s*["\']?([^"\'\s#]+)', text)
    if not secret_match:
        errors.append("secret_key is missing")
    else:
        secret = secret_match.group(1).strip()
        if secret == _PLACEHOLDER_SECRET:
            errors.append("secret_key still uses the example placeholder")
        elif len(secret) < 32:
            errors.append("secret_key is unexpectedly short; use a strong random value")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check searxng/settings.yml for required production invariants."
    )
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()

    if not args.path.is_file():
        print(f"ERROR: {args.path} does not exist")
        print("Create it from searxng/settings.example.yml, then keep the real secret_key.")
        return 2

    errors = validate_settings_text(args.path.read_text(encoding="utf-8"))
    if errors:
        print(f"ERROR: {args.path} is stale or unsafe:")
        for error in errors:
            print(f"- {error}")
        print("Merge searxng/settings.example.yml into the production file without replacing secret_key.")
        return 1

    print(f"OK: {args.path} matches required SearXNG production invariants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
