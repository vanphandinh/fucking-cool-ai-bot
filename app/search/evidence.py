"""Bounded, query-specific evidence extraction from untrusted web pages.

The source document remains data. Scoring never treats document instructions
as assistant instructions; output labels preserve where each excerpt came from.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

MAX_CANDIDATE_CHARS = 120_000
MAX_TOOL_EVIDENCE_CHARS = 5_400  # includes source labels, URL and headings
_WORDS = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_HEADINGS = re.compile(r"(?m)^\s*#{1,6}\s+(.+?)\s*$")
_STOPWORDS = frozenset(
    "a an and are as at by for from how in is of on or the to what when where which who "
    "bao bằng các cho có của đã được gì là một nào này ở và về với những số trong "
    "theo bao nhieu nhiêu hay la gi can cần hãy cho biết".split()
)
_SUMMARY = ("tóm tắt", "tom tat", "summarize", "summary", "overview", "tổng quan")

# Reserved framing tokens. Only renderers may emit these; untrusted page text
# that contains one is neutralized before any label is built, so a hostile page
# can neither close the synthesis frame early nor spoof a source attribution.
# A hostile page can re-space a reserved phrase ("DỮ-LIỆU", "DỮ.LIỆU", a
# stray newline before the bracket) so a model still reads it as a delimiter
# while a rigid pattern does not. ``_SEP`` absorbs those separators — and the
# closing bracket tolerates trailing whitespace — so look-alikes are defused
# too. Widening is safe: the marker is already reserved, so defusing a broader
# set of look-alikes can never remove legitimate evidence.
_SEP = r"[\s\-–—_.·•/\\|]{0,8}"
_CLOSE = r"[^\]］\n]{0,200}\s{0,8}[\]］]"
_FRAME_KEYWORDS = (
    rf"(?:DỮ|DU){_SEP}(?:LIỆU|LIEU){_SEP}(?:NGHIÊN|NGHIEN){_SEP}(?:CỨU|CUU)"
)
_OTHER_KEYWORDS = (
    rf"WEB{_SEP}EVIDENCE|WEB{_SEP}AUTO{_SEP}FETCH|TOOL[_\s]RESULT\s*\d*|AUTO{_SEP}SOURCE"
)
_SOURCE_KEYWORDS = rf"SOURCE{_SEP}\d+{_SEP}/{_SEP}CHUNK{_SEP}\d+"
_FRAME_ONLY_RE = re.compile(
    rf"[\[［]\s*/?\s*{_FRAME_KEYWORDS}{_CLOSE}",
    re.IGNORECASE,
)
_UNTRUSTED_RE = re.compile(
    rf"[\[［]\s*/?\s*(?:{_FRAME_KEYWORDS}|{_OTHER_KEYWORDS}){_CLOSE}",
    re.IGNORECASE,
)
_SOURCE_LABEL_RE = re.compile(
    rf"[\[［]\s*/?\s*(?:{_SOURCE_KEYWORDS}|AUTO{_SEP}SOURCE){_CLOSE}",
    re.IGNORECASE,
)
# Bracket glyphs that can no longer be parsed back into a system delimiter.
_BRACKET_TRANSLATION = str.maketrans({"[": "⟦", "]": "⟧", "［": "⟦", "］": "⟧"})


def _defuse_brackets(match: re.Match[str]) -> str:
    return match.group(0).translate(_BRACKET_TRANSLATION)


def defuse_frame_markers(text: str) -> str:
    """Defuse only the evidence-frame delimiters, keeping every other label."""
    return _FRAME_ONLY_RE.sub(_defuse_brackets, str(text or ""))


def neutralize_untrusted(text: str, *, keep_source_labels: bool = False) -> str:
    """Strip frame/source markers a hostile document might try to forge.

    ``[SOURCE n / CHUNK m]`` labels are renderer-only; callers that receive text
    already produced by ``select_evidence`` may keep them so genuine chunk
    attribution survives.
    """
    out = _UNTRUSTED_RE.sub(_defuse_brackets, str(text or ""))
    if not keep_source_labels:
        out = _SOURCE_LABEL_RE.sub(_defuse_brackets, out)
    return out


def normalize(text: str) -> str:
    """Casefold and strip Vietnamese accents for lexical matching."""
    folded = unicodedata.normalize("NFKD", text.casefold().replace("đ", "d"))
    return "".join(c for c in folded if not unicodedata.combining(c))


def terms(text: str) -> set[str]:
    return {w for w in _WORDS.findall(normalize(text)) if len(w) > 1 and w not in _STOPWORDS}


def relevance(query: str, body: str, *, title: str = "", heading: str = "") -> float:
    wanted = terms(query)
    if not wanted:
        return 0.0
    content = terms(body)
    headings = terms(f"{title} {heading}")
    matched = wanted & (content | headings)
    score = (len(matched) + len(wanted & headings)) / (2 * len(wanted))
    phrase = normalize(" ".join(query.split()))
    if phrase and len(phrase) > 9 and phrase in normalize(f"{heading} {body}"):
        score += 0.2
    return min(score, 1.0)


class _SectionParser(HTMLParser):
    """Stream HTML with a bounded text buffer and fail on oversized raw tokens."""

    _skip = frozenset({"script", "style", "nav", "footer", "header", "aside", "form", "noscript"})
    _blocks = frozenset({
        "p", "li", "ul", "ol", "article", "section", "div", "br", "tr", "blockquote",
    })

    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.parts: list[str] = []
        self.length = 0
        self.skip: str | None = None
        self.depth = 0
        self.heading: str | None = None

    def _append(self, text: str) -> None:
        if self.length >= self.limit:
            return
        text = text[: self.limit - self.length]
        if text:
            self.parts.append(text)
            self.length += len(text)

    def handle_starttag(self, tag: str, attrs) -> None:
        if self.skip:
            if tag == self.skip:
                self.depth += 1
            return
        if tag in self._skip:
            self.skip, self.depth = tag, 1
            return
        if re.fullmatch(r"h[1-6]", tag):
            self.heading = tag
            self._append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in self._blocks:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip:
                self.depth -= 1
                if self.depth == 0:
                    self.skip = None
            return
        if tag == self.heading:
            self.heading = None
        if tag in self._blocks or re.fullmatch(r"h[1-6]", tag):
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        data = " ".join(data.split())
        if not data:
            return
        if self.parts and not self.parts[-1].endswith((" ", "\n")):
            self._append(" ")
        self._append(data)


async def extract_html(body: bytes, encoding: str, max_chars: int) -> tuple[str, bool]:
    """Decode at most the existing HTTP byte limit, then retain <=120k text."""
    from .reader import _FetchError

    max_chars = min(max(1, max_chars), MAX_CANDIDATE_CHARS)
    try:
        markup = body.decode(encoding, errors="replace")
    except LookupError:
        markup = body.decode("utf-8", errors="replace")
    parser = _SectionParser(max_chars)
    for start in range(0, len(markup), 8192):
        parser.feed(markup[start : start + 8192])
        if len(parser.rawdata) > 65536:
            raise _FetchError("HTML chứa thẻ/chú thích quá dài")
        await asyncio.sleep(0)  # allow cancellation during long-page parsing
        if parser.length >= max_chars:
            break
    else:
        parser.close()
    content = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
    return content, parser.length >= max_chars


@dataclass(frozen=True)
class EvidenceChunk:
    ordinal: int
    heading: str
    text: str
    score: float


def split_chunks(
    text: str, query: str, *, title: str = "", size: int = 1000, overlap: int = 150,
) -> list[EvidenceChunk]:
    """Keep section attribution and overlap while avoiding mid-word splits."""
    headings = list(_HEADINGS.finditer(text))
    chunks: list[EvidenceChunk] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = max(
                text.rfind("\n", start + size // 2, end),
                text.rfind(" ", start + size // 2, end),
            )
            if boundary > start:
                end = boundary
        if end <= start:
            end = min(len(text), start + size)
        snippet = text[start:end].strip()
        heading = ""
        for match in headings:
            if match.start() > start:
                break
            heading = match.group(1).strip()[:160]
        if snippet:
            chunks.append(EvidenceChunk(
                len(chunks) + 1, heading, snippet,
                relevance(query, snippet, title=title, heading=heading),
            ))
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
        while start < end and start > 0 and not text[start - 1].isspace():
            start += 1
    return chunks


def select_evidence(
    text: str, source_url: str, query: str = "", *, max_chars: int = MAX_TOOL_EVIDENCE_CHARS,
    truncated: bool = False,
) -> str:
    """Rank relevant chunks, then pack complete labels and excerpts into a hard cap."""
    max_chars = min(MAX_TOOL_EVIDENCE_CHARS, max_chars)
    text = neutralize_untrusted(text)
    content = text[:MAX_CANDIDATE_CHARS]
    truncated = truncated or len(text) > len(content)
    if not content.strip():
        return "Không tìm được nội dung bài viết."
    title_match = re.search(r"(?m)^Title:\s*(.+?)\s*$", content)
    title = title_match.group(1)[:160] if title_match else ""
    chunks = split_chunks(content, query, title=title)
    if not chunks:
        return "Không tìm được nội dung bài viết."
    is_summary = not terms(query) or any(normalize(word) in normalize(query) for word in _SUMMARY)
    if is_summary:
        # Cover beginning, middle, end before filling other distinct sections.
        anchors = [0, len(chunks) // 2, len(chunks) - 1]
        order = list(dict.fromkeys(anchors))
        order.extend(i for i in range(len(chunks)) if i not in order)
    else:
        ranked = sorted(range(len(chunks)), key=lambda i: (-chunks[i].score, i))
        # Generic intros repeat query terms. Reserve space for section coverage
        # so a relevant fact deep in the document is not crowded out by intros.
        strong = [i for i in ranked if chunks[i].score >= 0.2][:2]
        coverage: list[int] = []
        for group in range(4):
            begin = len(chunks) * group // 4
            end = len(chunks) * (group + 1) // 4
            if begin >= end:
                continue
            middle = (begin + end - 1) / 2
            coverage.append(max(
                range(begin, end),
                key=lambda i: (chunks[i].score, -abs(i - middle)),
            ))
        # The conclusion/appendix often follows repeated low-value sections.
        # Give the final chunk an explicit slot even when all scores tie.
        order = list(dict.fromkeys([*strong, len(chunks) - 1, *coverage, *ranked]))
    domain = urlsplit(source_url).hostname or "unknown"
    header = (
        f"[WEB EVIDENCE — nội dung nguồn không tin cậy]\n"
        f"Nguồn: {source_url}\nDomain: {domain}\n"
    )
    if truncated:
        header += "Trạng thái: nguồn có thể đã bị cắt tại giới hạn trích xuất.\n"
    if len(header) >= max_chars:
        return "Không thể đóng gói chứng cứ: URL quá dài."
    parts = [header]
    left = max_chars - len(header)
    seen: set[str] = set()
    for i in order:
        chunk = chunks[i]
        signature = normalize(chunk.text[:250])
        if signature in seen:
            continue
        label = f"\n[SOURCE 1 / CHUNK {chunk.ordinal}] URL: {source_url}\n"
        if chunk.heading:
            label += f"Heading: {chunk.heading}\n"
        if len(label) + 1 > left:
            break
        payload = chunk.text[: min(len(chunk.text), left - len(label))].rstrip()
        if not payload:
            break
        parts.append(label + payload)
        left -= len(label) + len(payload)
        seen.add(signature)
    return "".join(parts)[:max_chars]
