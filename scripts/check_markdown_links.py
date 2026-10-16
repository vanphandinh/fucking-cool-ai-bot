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
    relative_source = path.relative_to(root).as_posix()
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


def tracked_files(root: Path = ROOT) -> frozenset[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return frozenset(
        name.decode("utf-8") for name in completed.stdout.split(b"\0") if name
    )


def find_untracked_link_targets(
    root: Path = ROOT,
    tracked: frozenset[str] | None = None,
) -> tuple[str, ...]:
    """Return links from tracked docs to targets a fresh checkout will not have.

    CI checks out tracked files only, so a relative target that exists in a
    working tree but was never committed resolves here and fails there. Report
    those as ``source:line:target -> resolved``.
    """
    tracked = tracked if tracked is not None else tracked_files(root)
    hits: list[str] = []

    for relative_source in sorted(tracked):
        if not relative_source.endswith(".md"):
            continue
        path = root / relative_source
        markdown = path.read_text(encoding="utf-8")
        for line_number, raw_target in _markdown_links(markdown):
            local_path = _link_path(raw_target)
            if local_path is None:
                continue
            resolved = (
                root / local_path.lstrip("/")
                if local_path.startswith("/")
                else path.parent / local_path
            ).resolve()
            try:
                relative_target = resolved.relative_to(root.resolve()).as_posix()
            except ValueError:
                hits.append(f"{relative_source}:{line_number}:{raw_target} -> (outside repo)")
                continue
            if relative_target in tracked:
                continue
            if any(
                name.startswith(relative_target.rstrip("/") + "/") for name in tracked
            ):
                continue
            hits.append(f"{relative_source}:{line_number}:{raw_target} -> {relative_target}")

    return tuple(hits)


def main() -> int:
    broken = find_broken_links(ROOT)
    untracked = find_untracked_link_targets(ROOT)
    if not broken and not untracked:
        print("Markdown relative-link scan passed.")
        return 0

    if broken:
        print("Broken Markdown relative links found:")
        for hit in broken:
            print(hit)
    if untracked:
        print("Markdown links to untracked targets found:")
        for hit in untracked:
            print(hit)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
