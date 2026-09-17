"""Check tracked Markdown files for broken local relative links."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt

ROOT = Path(__file__).parents[1]

_MARKDOWN = MarkdownIt("commonmark", {"store_labels": True})
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _link_path(raw_target: str) -> str | None:
    target = raw_target.strip()
    if not target or target.startswith("#") or target.startswith("//"):
        return None
    if _SCHEME_RE.match(target):
        return None

    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    return unquote(parsed.path)


def _markdown_links(markdown: str) -> tuple[tuple[int, str], ...]:
    env: dict[str, object] = {}
    tokens = _MARKDOWN.parse(markdown, env)
    references = env.get("references", {})
    links: list[tuple[int, str]] = []

    for token in tokens:
        if token.type != "inline" or token.map is None or not token.children:
            continue

        line_number = token.map[0] + 1
        for child in token.children:
            if child.type in {"softbreak", "hardbreak"}:
                line_number += 1
                continue
            if child.type == "link_open":
                raw_target = child.attrGet("href")
            elif child.type == "image":
                raw_target = child.attrGet("src")
            else:
                continue
            if raw_target is None:
                continue

            label = child.meta.get("label")
            if label and isinstance(references, dict):
                reference = references.get(label)
                if isinstance(reference, dict):
                    source_map = reference.get("map")
                    if isinstance(source_map, list) and source_map:
                        links.append((source_map[0] + 1, raw_target))
                        continue
            links.append((line_number, raw_target))

    return tuple(links)


def scan_markdown_file(path: Path, root: Path) -> tuple[str, ...]:
    """Return broken local links as ``source:line:target`` strings."""

    hits: list[str] = []
    relative_source = path.relative_to(root)
    markdown = path.read_text(encoding="utf-8")

    for line_number, raw_target in _markdown_links(markdown):
        local_path = _link_path(raw_target)
        if local_path is None:
            continue

        target = (
            root / local_path.lstrip("/")
            if local_path.startswith("/")
            else path.parent / local_path
        ).resolve()
        if not target.exists():
            hits.append(f"{relative_source}:{line_number}:{raw_target}")

    return tuple(hits)


def tracked_markdown_files(root: Path = ROOT) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z", "*.md"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    names = tuple(name for name in completed.stdout.split(b"\0") if name)
    return tuple(root / name.decode("utf-8") for name in names)


def find_broken_links(root: Path = ROOT) -> tuple[str, ...]:
    hits: list[str] = []
    for path in tracked_markdown_files(root):
        hits.extend(scan_markdown_file(path, root))
    return tuple(hits)


def main() -> int:
    hits = find_broken_links(ROOT)
    if not hits:
        print("Markdown relative-link scan passed.")
        return 0

    print("Broken Markdown relative links found:")
    for hit in hits:
        print(hit)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
