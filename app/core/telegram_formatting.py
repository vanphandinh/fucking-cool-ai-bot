"""Telegram-native HTML normalization, sanitization, and safe splitting."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

_TAG_ALIASES = {
    "b": "b",
    "strong": "b",
    "i": "i",
    "em": "i",
    "u": "u",
    "ins": "u",
    "s": "s",
    "strike": "s",
    "del": "s",
    "tg-spoiler": "tg-spoiler",
    "code": "code",
    "pre": "pre",
    "blockquote": "blockquote",
    "a": "a",
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+)$")
_BOLD_RE = re.compile(r"\*\*([^\n*][^\n]*?)\*\*")
_INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_PROTECTED_HTML_RE = re.compile(
    r"<(pre|code)\b[^>]*>.*?</\1\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_PLACEHOLDER_RE = re.compile(r"\x00PROTECTED_(\d+)\x00")


def _protect_existing_code_html(text: str, protected: list[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00PROTECTED_{len(protected) - 1}\x00"

    return _PROTECTED_HTML_RE.sub(repl, text)


def _restore_protected_html(text: str, protected: list[str]) -> str:
    def repl(match: re.Match[str]) -> str:
        return protected[int(match.group(1))]

    return _PLACEHOLDER_RE.sub(repl, text)


def _convert_fenced_code(text: str) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.rstrip("\r\n")
        if not stripped.startswith("```"):
            out.append(line)
            index += 1
            continue

        language = stripped[3:].strip()
        if language and not re.fullmatch(r"[A-Za-z0-9_+.-]+", language):
            out.append(line)
            index += 1
            continue

        body: list[str] = []
        index += 1
        closed = False
        closing_suffix = ""
        while index < len(lines):
            candidate = lines[index]
            if candidate.rstrip("\r\n").strip() == "```":
                closed = True
                if candidate.endswith("\r\n"):
                    closing_suffix = "\r\n"
                elif candidate.endswith("\n") or candidate.endswith("\r"):
                    closing_suffix = candidate[-1]
                index += 1
                break
            body.append(candidate)
            index += 1

        if not closed:
            out.append(line)
            out.extend(body)
            continue

        body_text = "".join(body)
        out.append(f"<pre>{html.escape(body_text, quote=False)}</pre>{closing_suffix}")
    return "".join(out)


def _normalize_markdown_line(line: str) -> str:
    newline = ""
    if line.endswith("\r\n"):
        content = line[:-2]
        newline = "\r\n"
    elif line.endswith("\n") or line.endswith("\r"):
        content = line[:-1]
        newline = line[-1]
    else:
        content = line

    heading = _HEADING_RE.match(content)
    if heading:
        content = f"<b>{heading.group(2)}</b>"
    else:
        bullet = _BULLET_RE.match(content)
        if bullet:
            content = f"• {bullet.group(1)}"

    content = _BOLD_RE.sub(r"<b>\1</b>", content)
    content = _INLINE_CODE_RE.sub(r"<code>\1</code>", content)
    return content + newline


def _normalize_legacy_markdown(text: str) -> str:
    protected: list[str] = []
    protected_text = _protect_existing_code_html(text, protected)
    fenced = _convert_fenced_code(protected_text)
    fenced = _protect_existing_code_html(fenced, protected)
    normalized = "".join(
        _normalize_markdown_line(line) for line in fenced.splitlines(keepends=True)
    )
    return _restore_protected_html(normalized, protected)


def _valid_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


@dataclass(frozen=True)
class _OpenTag:
    canonical: str
    open_html: str
    close_html: str


class _TelegramSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[_OpenTag] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        canonical = _TAG_ALIASES.get(tag.lower())
        if canonical is None:
            return

        if canonical == "a":
            href = next((value for name, value in attrs if name.lower() == "href"), None) or ""
            if not _valid_http_url(href):
                return
            open_html = f'<a href="{html.escape(href, quote=True)}">'
        elif canonical == "blockquote":
            expandable = any(name.lower() == "expandable" for name, _ in attrs)
            open_html = "<blockquote expandable>" if expandable else "<blockquote>"
        else:
            open_html = f"<{canonical}>"

        opened = _OpenTag(canonical, open_html, f"</{canonical}>")
        self.stack.append(opened)
        self.out.append(opened.open_html)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        before = len(self.stack)
        self.handle_starttag(tag, attrs)
        if len(self.stack) > before:
            opened = self.stack.pop()
            self.out.append(opened.close_html)

    def handle_endtag(self, tag: str) -> None:
        canonical = _TAG_ALIASES.get(tag.lower())
        if canonical is None:
            return

        match_index = next(
            (
                index
                for index in range(len(self.stack) - 1, -1, -1)
                if self.stack[index].canonical == canonical
            ),
            None,
        )
        if match_index is None:
            return

        while len(self.stack) > match_index:
            self.out.append(self.stack.pop().close_html)

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data, quote=False))

    def finish(self) -> str:
        while self.stack:
            self.out.append(self.stack.pop().close_html)
        return "".join(self.out)


def sanitize_telegram_html(text: str) -> str:
    normalized = _normalize_legacy_markdown(text or "")
    parser = _TelegramSanitizer()
    parser.feed(normalized)
    parser.close()
    return parser.finish()


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []

    def handle_data(self, data: str) -> None:
        self.out.append(data)

    def finish(self) -> str:
        return "".join(self.out)


def telegram_html_to_plain(text: str) -> str:
    parser = _PlainTextParser()
    parser.feed(text or "")
    parser.close()
    return parser.finish()


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _prefix_fitting(text: str, limit: int) -> int:
    if limit <= 0:
        return 0
    if _utf16_len(text) <= limit:
        return len(text)
    raw = text.encode("utf-16-le")
    chunk = raw[: limit * 2]
    if len(chunk) >= 2:
        last = int.from_bytes(chunk[-2:], "little")
        if 0xD800 <= last <= 0xDBFF:
            chunk = chunk[:-2]
    return len(chunk.decode("utf-16-le"))


def _find_visible_cut(text: str, limit: int) -> int:
    max_i = _prefix_fitting(text, limit)
    if max_i <= 0 or max_i == len(text):
        return max_i
    for delim in ("\n\n", "\n", ". ", "? ", "! "):
        pos = text.rfind(delim, 0, max_i)
        if pos != -1 and max_i - pos <= 600:
            return pos + len(delim)
    return max_i


@dataclass(frozen=True)
class _StartTag:
    canonical: str
    open_html: str
    close_html: str


@dataclass(frozen=True)
class _Text:
    value: str


@dataclass(frozen=True)
class _EndTag:
    canonical: str
    close_html: str


class _SafeHtmlTokenizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[_StartTag | _Text | _EndTag] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        canonical = tag.lower()
        if canonical == "a":
            href = next((value for name, value in attrs if name.lower() == "href"), None) or ""
            open_html = f'<a href="{html.escape(href, quote=True)}">'
        elif canonical == "blockquote":
            expandable = any(name.lower() == "expandable" for name, _ in attrs)
            open_html = "<blockquote expandable>" if expandable else "<blockquote>"
        else:
            open_html = f"<{canonical}>"
        self.events.append(_StartTag(canonical, open_html, f"</{canonical}>"))

    def handle_endtag(self, tag: str) -> None:
        canonical = tag.lower()
        self.events.append(_EndTag(canonical, f"</{canonical}>"))

    def handle_data(self, data: str) -> None:
        if data:
            self.events.append(_Text(data))


def _tokenize_safe_html(text: str) -> list[_StartTag | _Text | _EndTag]:
    parser = _SafeHtmlTokenizer()
    parser.feed(text)
    parser.close()
    return parser.events


def split_telegram_html(text: str, limit: int = 3900) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")

    safe = sanitize_telegram_html(text)
    if not safe:
        return []

    events = _tokenize_safe_html(safe)
    parts: list[str] = []
    current: list[str] = []
    active: list[_StartTag] = []
    visible_len = 0

    def flush() -> None:
        nonlocal current, visible_len
        if visible_len == 0:
            return
        current.extend(tag.close_html for tag in reversed(active))
        parts.append("".join(current))
        current = [tag.open_html for tag in active]
        visible_len = 0

    for event in events:
        if isinstance(event, _StartTag):
            current.append(event.open_html)
            active.append(event)
            continue
        if isinstance(event, _EndTag):
            if active and active[-1].canonical == event.canonical:
                active.pop()
                current.append(event.close_html)
            continue

        remaining_text = event.value
        while remaining_text:
            remaining_limit = limit - visible_len
            if remaining_limit <= 0:
                flush()
                remaining_limit = limit
            cut = _find_visible_cut(remaining_text, remaining_limit)
            if cut <= 0:
                flush()
                continue
            piece = remaining_text[:cut]
            current.append(html.escape(piece, quote=False))
            visible_len += _utf16_len(piece)
            remaining_text = remaining_text[cut:]
            if remaining_text:
                flush()

    if visible_len > 0:
        current.extend(tag.close_html for tag in reversed(active))
        parts.append("".join(current))
    return parts
