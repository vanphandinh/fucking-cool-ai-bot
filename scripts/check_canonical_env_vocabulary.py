from __future__ import annotations

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).parents[1]

_RETIRED_IDENTIFIER_PARTS: tuple[tuple[str, ...], ...] = (
    ("CHAINNODE_API_", "KEY"),
    ("CHAINNODE_TEXT_", "MODEL"),
    ("CHAINNODE_VISION_", "MODEL"),
    ("XKIRO_API_", "KEY"),
    ("XKIRO_PROBE_API_", "KEY"),
    ("XKIRO_TEXT_", "MODEL"),
    ("XKIRO_VISION_", "MODEL"),
    ("CHAINNODE_API_", "KEYS"),
    ("CHAINNODE_BASE_", "URL"),
    ("CHAINNODE_TEXT_", "MODELS"),
    ("CHAINNODE_VISION_", "MODELS"),
    ("CHAINNODE_REQUEST_TIMEOUT_", "SEC"),
    ("XKIRO_API_", "KEYS"),
    ("XKIRO_BASE_", "URL"),
    ("XKIRO_TEXT_", "MODELS"),
    ("XKIRO_VISION_", "MODELS"),
    ("XKIRO_REQUEST_TIMEOUT_", "SEC"),
    ("PROVIDER_RETRY_MAX_CONSECUTIVE_", "FAILURES"),
    ("PROVIDER_RETRY_MAX_", "FAILURES_PER_PROVIDER"),
    ("PROVIDER_RETRY_MAX_", "FAILURES_PER_REQUEST"),
    ("chainnode_api_", "key"),
    ("chainnode_text_", "model"),
    ("chainnode_vision_", "model"),
    ("xkiro_api_", "key"),
    ("xkiro_probe_api_", "key"),
    ("xkiro_text_", "model"),
    ("xkiro_vision_", "model"),
    ("chainnode_api_", "keys"),
    ("chainnode_api_keys_", "list"),
    ("chainnode_base_", "url"),
    ("chainnode_text_", "models"),
    ("chainnode_text_models_", "list"),
    ("chainnode_vision_", "models"),
    ("chainnode_vision_models_", "list"),
    ("chainnode_request_timeout_", "sec"),
    ("xkiro_api_", "keys"),
    ("xkiro_api_keys_", "list"),
    ("xkiro_base_", "url"),
    ("xkiro_text_", "models"),
    ("xkiro_text_models_", "list"),
    ("xkiro_vision_", "models"),
    ("xkiro_vision_models_", "list"),
    ("xkiro_request_timeout_", "sec"),
    ("provider_retry_max_consecutive_", "failures"),
    ("provider_retry_max_", "failures_per_provider"),
    ("provider_retry_max_", "failures_per_request"),
)
_RETIRED_PROVIDER_PATTERN_PARTS: tuple[tuple[str, ...], ...] = (
    (r"app\.ai\.", "b", "ai"),
    ("build_", "b", "ai"),
    ("make_", "b", "ai"),
    ("B", "AI_"),
    (r"api\.b\.", "ai"),
    (r"\b", "b", "ai", r"\b"),
)
_RETIRED_PROVIDER_PATTERN = re.compile(
    "|".join("".join(parts) for parts in _RETIRED_PROVIDER_PATTERN_PARTS),
    flags=re.IGNORECASE,
)


def retired_identifiers() -> tuple[str, ...]:
    return tuple("".join(parts) for parts in _RETIRED_IDENTIFIER_PARTS)


def _pattern(identifier: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])"
    )


def scan_text(text: str) -> tuple[str, ...]:
    hits = [
        identifier
        for identifier in retired_identifiers()
        if _pattern(identifier).search(text)
    ]
    hits.extend(match.group(0) for match in _RETIRED_PROVIDER_PATTERN.finditer(text))
    return tuple(hits)


def tracked_files(root: Path) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    names = tuple(name for name in completed.stdout.split(b"\0") if name)
    return tuple(root / name.decode("utf-8") for name in names)


def scan_repository(root: Path = ROOT) -> tuple[str, ...]:
    hits: list[str] = []

    for path in tracked_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        relative = path.relative_to(root)
        if relative.parts[:2] == ("docs", "superpowers"):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for retired in scan_text(line):
                hits.append(f"{relative}:{line_number}:{retired}")

    return tuple(hits)


def main() -> int:
    hits = scan_repository(ROOT)
    if not hits:
        print("Canonical environment vocabulary scan passed.")
        return 0

    print("Retired environment/provider identifiers found:")
    for hit in hits:
        print(hit)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
