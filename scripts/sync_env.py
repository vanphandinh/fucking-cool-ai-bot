from pathlib import Path
import os
import re
import stat
import sys
import tempfile


_ASSIGNMENT_RE = re.compile(
    r"^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$"
)


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
    current = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    current_values = _parse_values(current)

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
    if env_path.exists() and rendered == current:
        return

    source_mode = env_path.stat().st_mode if env_path.exists() else example_path.stat().st_mode
    _atomic_write(env_path, rendered, stat.S_IMODE(source_mode))


def main() -> int:
    try:
        _sync()
    except (OSError, ValueError) as exc:
        print(f"sync-env: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
