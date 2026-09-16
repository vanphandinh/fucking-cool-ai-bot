from pathlib import Path
import os
import re
import stat
import sys
import tempfile


_ASSIGNMENT_RE = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$"
)
_INLINE_COMMENT_RE = re.compile(r"\s+#")
_PRIVATE_ENV_MODE = 0o600
_PROVIDER_ORDER_KEYS = ("TEXT_PROVIDER_ORDER", "VISION_PROVIDER_ORDER")
_FALSE_BOOL_VALUES = {"0", "false", "f", "no", "n", "off"}
_ENV_MIGRATIONS: dict[str, tuple[str, ...]] = {
    "PROVIDER_RETRY_MAX_CONSECUTIVE": (
        "PROVIDER_RETRY_MAX_CONSECUTIVE_FAILURES",
    ),
    "PROVIDER_RETRY_MAX_PER_PROVIDER": (
        "PROVIDER_RETRY_MAX_FAILURES_PER_PROVIDER",
    ),
    "PROVIDER_RETRY_MAX_PER_REQUEST": (
        "PROVIDER_RETRY_MAX_FAILURES_PER_REQUEST",
    ),
}
_PROVIDER_POOL_MIGRATIONS: dict[str, str] = {
    "CHAINNODE_TEXT_MODELS": "CHAINNODE_TEXT_MODEL",
    "CHAINNODE_VISION_MODELS": "CHAINNODE_VISION_MODEL",
    "XKIRO_API_KEYS": "XKIRO_API_KEY",
    "XKIRO_TEXT_MODELS": "XKIRO_TEXT_MODEL",
    "XKIRO_VISION_MODELS": "XKIRO_VISION_MODEL",
}


def _split_assignment(line: str, source: str) -> tuple[str, str]:
    match = _ASSIGNMENT_RE.match(line)
    if match is None:
        raise ValueError(f"invalid assignment in {source}: {line!r}")
    return match.group(1), match.group(2)


def _parse_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, value = _split_assignment(line, ".env")
        if key in values:
            raise ValueError(f"duplicate key in .env: {key}")
        values[key] = value
    return values


def _dotenv_scalar_value(raw: str, *, label: str) -> str:
    value = raw.strip()
    if not value:
        return ""

    quote = value[0]
    if quote not in {"'", '"'}:
        unquoted = raw.rstrip()
        match = _INLINE_COMMENT_RE.search(unquoted)
        if match is not None:
            unquoted = unquoted[: match.start()].rstrip()
        return unquoted.strip()

    escaped = False
    closing_index: int | None = None
    for index, char in enumerate(value[1:], start=1):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == quote:
            closing_index = index
            break
    if closing_index is None:
        raise ValueError(f"invalid {label}: unclosed quoted value")

    trailing = value[closing_index + 1 :].strip()
    if trailing and not trailing.startswith("#"):
        raise ValueError(f"invalid {label}: unexpected content after quoted value")
    return value[1:closing_index]


def _provider_order_value(raw: str) -> str:
    return _dotenv_scalar_value(raw, label="provider order")


def _provider_order_names(raw: str) -> list[str]:
    value = _provider_order_value(raw)
    return [part.strip().lower() for part in value.split(",") if part.strip()]


def _migrate_provider_order(raw: str) -> str:
    names = _provider_order_names(raw)
    if "bai" not in names:
        return raw
    custom = [name for name in names if name not in {"bai", "chainnode", "xkiro"}]
    ordered = ["chainnode", "xkiro", *custom]
    return ",".join(dict.fromkeys(ordered))


def _migrate_values(values: dict[str, str]) -> dict[str, str]:
    migrated = dict(values)
    for canonical, legacy_names in _ENV_MIGRATIONS.items():
        if canonical in values:
            continue
        for legacy_name in legacy_names:
            if legacy_name in values:
                migrated[canonical] = values[legacy_name]
                break
    for canonical, legacy_name in _PROVIDER_POOL_MIGRATIONS.items():
        if canonical not in values and legacy_name in values:
            # Preserve the raw dotenv representation exactly: API keys and model
            # IDs are case-sensitive and may be quoted/commented.
            migrated[canonical] = values[legacy_name]
    for key in _PROVIDER_ORDER_KEYS:
        if key in migrated:
            migrated[key] = _migrate_provider_order(migrated[key])
    return migrated


def _configured(values: dict[str, str], key: str) -> bool:
    raw = _dotenv_scalar_value(values.get(key, ""), label=key)
    return bool(raw.strip())


def _enabled(values: dict[str, str], key: str, *, default: bool) -> bool:
    if key not in values:
        return default
    raw = _dotenv_scalar_value(values[key], label=key).strip().casefold()
    if raw in _FALSE_BOOL_VALUES:
        return False
    return True


def _has_replacement_text_provider(values: dict[str, str]) -> bool:
    names = _provider_order_names(values.get("TEXT_PROVIDER_ORDER", ""))
    if (
        "chainnode" in names
        and _configured(values, "CHAINNODE_API_KEY")
        and _configured(values, "CHAINNODE_TEXT_MODELS")
    ):
        return True
    return (
        "xkiro" in names
        and _configured(values, "XKIRO_API_KEYS")
        and _configured(values, "XKIRO_TEXT_MODELS")
    )


def _has_replacement_vision_provider(values: dict[str, str]) -> bool:
    names = _provider_order_names(values.get("VISION_PROVIDER_ORDER", ""))
    if (
        "chainnode" in names
        and _configured(values, "CHAINNODE_API_KEY")
        and _configured(values, "CHAINNODE_VISION_MODELS")
    ):
        return True
    return (
        "xkiro" in names
        and _configured(values, "XKIRO_API_KEYS")
        and _configured(values, "XKIRO_VISION_MODELS")
    )


def _validate_legacy_text_retirement(
    original_values: dict[str, str],
    effective_values: dict[str, str],
) -> None:
    legacy_provider = "bai"
    raw_order = original_values.get("TEXT_PROVIDER_ORDER", legacy_provider)
    if legacy_provider not in _provider_order_names(raw_order):
        return

    legacy_prefix = legacy_provider.upper()
    if not _configured(original_values, f"{legacy_prefix}_API_KEY"):
        return
    if not _has_replacement_text_provider(effective_values):
        raise ValueError(
            "cannot retire configured B.AI text route without a replacement text provider; "
            "configure Chainnode or xKiro credentials/model first"
        )


def _validate_legacy_vision_retirement(
    original_values: dict[str, str],
    effective_values: dict[str, str],
) -> None:
    legacy_provider = "bai"
    if "VISION_PROVIDER_ORDER" not in effective_values:
        return
    raw_order = original_values.get("VISION_PROVIDER_ORDER", legacy_provider)
    if legacy_provider not in _provider_order_names(raw_order):
        return
    if not _enabled(effective_values, "VISION_ENABLED", default=True):
        return

    legacy_prefix = legacy_provider.upper()
    if not _configured(original_values, f"{legacy_prefix}_API_KEY"):
        return
    if not _has_replacement_vision_provider(effective_values):
        raise ValueError(
            "cannot retire configured B.AI vision route without a replacement vision provider; "
            "configure Chainnode or xKiro vision credentials/model first, or disable vision"
        )


def _owner_only_mode(mode: int) -> int:
    """Drop execute/group/other bits while preserving stricter owner permissions."""
    return stat.S_IMODE(mode) & _PRIVATE_ENV_MODE


def _atomic_write(path: Path, content: str, mode: int) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def _sync() -> None:
    example_path = Path(".env.example")
    env_path = Path(".env")
    example = example_path.read_text(encoding="utf-8")
    env_exists = env_path.exists()
    current = env_path.read_text(encoding="utf-8") if env_exists else ""

    example_values = _parse_values(example)
    original_values = _parse_values(current)
    current_values = _migrate_values(original_values)
    effective_values = dict(example_values)
    effective_values.update(current_values)
    _validate_legacy_text_retirement(original_values, effective_values)
    _validate_legacy_vision_retirement(original_values, effective_values)

    output: list[str] = []
    seen_keys: set[str] = set()
    for line in example.splitlines(keepends=True):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            output.append(line)
            continue

        content = line.rstrip("\r\n")
        newline = line[len(content) :]
        key, default = _split_assignment(content, ".env.example")
        if key in seen_keys:
            raise ValueError(f"duplicate key in .env.example: {key}")
        seen_keys.add(key)

        lhs = content.split("=", 1)[0]
        value = current_values.get(key, default)
        output.append(f"{lhs}={value}{newline}")

    rendered = "".join(output)
    if env_exists:
        current_mode = stat.S_IMODE(env_path.stat().st_mode)
        private_mode = _owner_only_mode(current_mode)
        if rendered == current:
            if private_mode != current_mode:
                os.chmod(env_path, private_mode)
            return
    else:
        private_mode = _PRIVATE_ENV_MODE

    _atomic_write(env_path, rendered, private_mode)


def main() -> int:
    try:
        _sync()
    except (OSError, ValueError) as exc:
        print(f"sync-env: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
