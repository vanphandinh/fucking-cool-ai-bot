"""Deterministic citation guard for Web Evidence V2 answers.

Grounding is enforced in code, not only in prompt wording. After the model
answers, every URL-shaped citation is compared against the URLs that were
actually placed in the generation context. A citation whose URL is not in that
evidence loses its hyperlink and is explicitly marked as unverified, and the
event is recorded in diagnostics.

The guard is intentionally narrow:

* it never fetches a URL to legitimize a citation (an invented source must not
  be able to launder itself through one extra request);
* it never calls a model and never inspects claims or timestamps;
* it reuses the project's existing URL normalization policy
  (:func:`app.core.source_policy.canonicalize_source_url`), so two spellings of
  the same evidence URL compare equal;
* it preserves the Telegram HTML/Markdown shape of the surrounding answer.

Only the V2 answer path applies it; V1 answers are never rewritten.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ..core.source_policy import canonicalize_source_url

# Rendered next to a citation whose URL was not in the evidence. Kept short and
# language-matched to the rest of the product output.
UNVERIFIED_MARKER = "[nguồn chưa xác minh]"

_REASON_NOT_IN_EVIDENCE = "not_in_evidence"
_REASON_UNPARSEABLE = "unparseable_url"
_REASON_GUARD_ERROR = "guard_error"

# Sentence punctuation is not part of a URL, but a greedy scan cannot tell
# ``https://example.org/report.`` from a URL whose path really ends in a dot.
# The punctuation is trimmed only for the comparison and reinstated verbatim, so
# a citation that merely ends a sentence is not marked as unverified.
_TRAILING_PUNCTUATION = ".,;:!?"

# One ordered alternation so a match is consumed exactly once, left to right:
# an HTML anchor wins over a bare URL inside its own text.
#
# The anchor branch matches any href spelling a renderer can turn into a link:
# double-quoted, single-quoted, and unquoted attribute values — the literal
# ``https?://`` requirement moves from the href into the decision logic, which
# decodes character references first (postal pipeline: sanitize_telegram_html
# parses with convert_charrefs=True, so "&#104;ttps://..." renders as a real
# https:// link and must be judged by its decoded spelling).
#
# The autolink branch covers ``<https://...>`` so the replacement cannot leave
# an angle-bracket pseudo tag in the answer.
_URL_PATTERN = re.compile(
    r"<a\b[^>]*?href\s*=\s*"
    r"(?:\"(?P<html_url>[^\"'>]*)\"|'(?P<html_url_sq>[^'\">]*)'|(?P<html_url_uq>[^>\s]*))"
    r"[^>]*>(?P<html_text>.*?)</a>"
    r"|<(?P<autolink_url>https?://[^<>\s]+)>"
    r"|\[(?P<md_text>[^\]]*)\]\((?P<md_url>https?://[^\s)]+)\)"
    r"|(?P<bare_url>https?://[^\s<>\"'\)\]]+)",
    re.IGNORECASE | re.DOTALL,
)

# Character references, the subset the sanitizer's HTMLParser actually
# decodes. Known encodings are only three characters from any URL: h, t, p,
# s, : and / — they all can hide a citation from a literal scan.
_CHARREF_RE = re.compile(
    r"&#\d+;|&#x[0-9a-fA-F]+;|&[A-Za-z][A-Za-z0-9]*;",
)


@dataclass(frozen=True)
class CitationDiagnostics:
    """What the guard saw and did, for logging and tests.

    ``detected`` counts every URL-shaped citation found, ``allowed`` those whose
    canonical URL was in the evidence, ``removed`` those hyperlinks that were
    stripped and marked. ``reasons`` maps a removal reason to a count; ``error``
    is non-empty only when the normal pass failed and the fail-closed pass ran.
    """

    detected: int = 0
    allowed: int = 0
    removed: int = 0
    reasons: Mapping[str, int] = field(default_factory=dict)
    error: str = ""

    @property
    def unverified(self) -> int:
        return self.removed

    def as_dict(self) -> dict:
        return {
            "detected": self.detected,
            "allowed": self.allowed,
            "removed": self.removed,
            "reasons": dict(self.reasons),
            "error": self.error,
        }


def normalize_allowed_urls(allowed_urls: Iterable[object] | None) -> set[str]:
    """Canonicalize the evidence URL set the guard compares against."""
    normalized: set[str] = set()
    for value in allowed_urls or ():
        canonical = canonicalize_source_url(value)
        if canonical:
            normalized.add(canonical)
    return normalized


@dataclass(frozen=True)
class _DecodedText:
    """A text with character references decoded, mapped back to raw slices.

    ``text[i]`` renders exactly like ``source[span(i)[0]:span(i)[1]]`` in an
    HTML-aware renderer, because decoders (both the sanitizer ahead of the
    Telegram send path and ``html.unescape`` here) resolve numeric and named
    character references. Anchors that decode to zero characters cannot
    happen; every decoded character knows the raw slice it came from.
    """

    text: str
    spans: list[tuple[int, int]] | None  # None = identity (no charref found)


def _decode_charrefs(source: str) -> _DecodedText:
    if "&" not in source:
        return _DecodedText(source, None)
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    scan_at = 0
    size = len(source)
    while scan_at < size:
        match = _CHARREF_RE.match(source, scan_at)
        if match is None:
            chars.append(source[scan_at])
            spans.append((scan_at, scan_at + 1))
            scan_at += 1
            continue
        referring = match.group(0)
        value = html.unescape(referring)
        for char in value:
            chars.append(char)
            spans.append((scan_at, scan_at + len(referring)))
        scan_at = match.end()
    return _DecodedText("".join(chars), spans)


def _raw_span(decoded: _DecodedText, start: int, end: int) -> tuple[int, int]:
    """Map a decoded slice onto its contiguous raw slice."""
    if decoded.spans is None:
        return start, end
    return decoded.spans[start][0], decoded.spans[end - 1][1]


def enforce_v2_citations(
    text: str,
    allowed_urls: Iterable[object] | None,
) -> tuple[str, CitationDiagnostics]:
    """Strip and mark citations whose URL is not in ``allowed_urls``.

    Returns the rewritten answer and diagnostics. The function is total: if any
    step raises — including normalizing the allowed set — a dependency-free
    fail-closed pass marks every URL it finds instead of letting an unverified
    citation through unchanged. When the allowed set itself cannot be read, it
    is treated as empty, so nothing is trusted by default.
    """
    source = "" if text is None else str(text)
    allowed: set[str] = set()
    try:
        allowed = normalize_allowed_urls(allowed_urls)
        return _enforce(source, allowed)
    except Exception as exc:  # noqa: BLE001 - must degrade, never propagate
        return _fail_closed(source, allowed, exc)


def _enforce(source: str, allowed: set[str]) -> tuple[str, CitationDiagnostics]:
    counts = {"detected": 0, "allowed": 0, "removed": 0}
    reasons: dict[str, int] = {}
    decoded = _decode_charrefs(source)
    output: list[str] = []
    prev_raw = 0

    for match in _URL_PATTERN.finditer(decoded.text):
        url, kind, label = _match_parts(match)
        if url is None:
            continue
        if kind == "html" and not url.lower().startswith(("http://", "https://")):
            # The href does not render as a web link (mailto:, empty, hostless
            # or schemeless). The sanitizer drops such tags; leave them alone.
            continue
        counts["detected"] += 1
        core, trailing = _split_trailing_punctuation(url)
        canonical = canonicalize_source_url(core)
        raw_start, raw_end = _raw_span(decoded, match.start(), match.end())
        if canonical and canonical in allowed:
            counts["allowed"] += 1
            # The link itself is verified, but its visible content is rendered
            # too. A nested anchor or a second URL inside that content would
            # ride in without ever being counted by this loop, so rerun the
            # same rules on the citation's own text.
            raw_region = source[raw_start:raw_end]
            nested = _rewrite_citation_content(raw_region, kind, allowed)
            if nested is not None:
                rewritten, inner_diag = nested
                counts["detected"] += inner_diag.detected
                counts["allowed"] += inner_diag.allowed
                counts["removed"] += inner_diag.removed
                for reason, hits in inner_diag.reasons.items():
                    reasons[reason] = reasons.get(reason, 0) + hits
                output.append(source[prev_raw:raw_start])
                output.append(rewritten)
                prev_raw = raw_end
            continue  # otherwise keep the raw verbatim slice
        counts["removed"] += 1
        reason = _REASON_NOT_IN_EVIDENCE if canonical else _REASON_UNPARSEABLE
        reasons[reason] = reasons.get(reason, 0) + 1
        output.append(source[prev_raw:raw_start])
        output.append(_marked(kind, label) + trailing)
        prev_raw = raw_end
    output.append(source[prev_raw:])
    rewritten = "".join(output)
    return rewritten, CitationDiagnostics(
        detected=counts["detected"],
        allowed=counts["allowed"],
        removed=counts["removed"],
        reasons=reasons,
    )


def _match_parts(match: re.Match[str]) -> tuple[str | None, str, str]:
    for name in ("html_url", "html_url_sq", "html_url_uq"):
        href = match.group(name)
        if href is not None:
            # Decoding here, not in _enforce: a quoted-href value is what the
            # renderer resolves for the link target, not what it displays.
            return html.unescape(href), "html", match.group("html_text") or ""
    autolink_url = match.group("autolink_url")
    if autolink_url is not None:
        return autolink_url, "autolink", ""
    md_url = match.group("md_url")
    if md_url is not None:
        return md_url, "md", match.group("md_text") or ""
    bare_url = match.group("bare_url")
    if bare_url is not None:
        return bare_url, "bare", bare_url
    return None, "", ""


def _rewrite_citation_content(
    raw_region: str,
    kind: str,
    allowed: set[str],
) -> tuple[str, CitationDiagnostics] | None:
    """Re-enforce the visible text of an allowed citation, if it contains URLs.

    Returns ``None`` when the content has nothing to enforce (the common case,
    so keeping the raw slice verbatim stays a no-op). The link structure of the
    citation itself is preserved: an HTML anchor keeps its opening tag and
    ``</a>``, a Markdown link keeps ``[label](url)``; only the label may gain
    unverified-citation markers.
    """
    if kind not in ("html", "md"):
        return None
    if kind == "html":
        gt = raw_region.find(">")
        if gt < 0 or not raw_region.endswith("</a>"):
            return None
        inner = raw_region[gt + 1 : -4]
        prefix, suffix = raw_region[: gt + 1], "</a>"
    else:
        bracket = raw_region.find("](")
        if bracket < 0 or not raw_region.startswith("["):
            return None
        inner = raw_region[1:bracket]
        suffix = raw_region[bracket:]
        prefix = "["
    if _URL_PATTERN.search(inner) is None:
        return None
    rewritten, diag = _enforce(inner, allowed)
    if rewritten == inner:
        return None
    return f"{prefix}{rewritten}{suffix}", diag


def _split_trailing_punctuation(url: str) -> tuple[str, str]:
    """Split sentence punctuation off a scanned URL candidate."""
    end = len(url)
    while end > 0 and url[end - 1] in _TRAILING_PUNCTUATION:
        end -= 1
    return url[:end], url[end:]


def _marked(kind: str, label: str) -> str:
    """Keep the visible text of a link, drop the hyperlink, add the marker."""
    label = label.strip()
    if kind in ("html", "md") and label:
        return f"{label} {UNVERIFIED_MARKER}"
    return UNVERIFIED_MARKER


def _fail_closed(
    source: str, allowed: set[str], exc: Exception
) -> tuple[str, CitationDiagnostics]:
    """Emergency pass using only string operations and exact allowed matches.

    The invariant it must preserve is the one the plan requires: an unverified
    citation is never allowed through without a marker. Being stricter than the
    normal pass (marking more) is acceptable here.
    """
    kept = 0
    removed = 0
    rebuilt: list[str] = []
    offset = 0
    # Do not call the normal URL parser here: it is allowed to be the source of
    # the failure (for example a query containing a lone Unicode surrogate).
    # Scan the original string: lowercasing the whole answer can change its
    # length (e.g. Turkish dotted İ), invalidating the match offsets.
    scan_at = 0
    while scan_at < len(source):
        if source[scan_at : scan_at + 4].lower() != "http":
            scan_at += 1
            continue
        start = scan_at
        rebuilt.append(source[offset:start])
        end = start + 4
        while end < len(source) and not source[end].isspace() and source[end] not in "<>\"'":
            end += 1
        raw_url = source[start:end]
        url = raw_url.rstrip(_TRAILING_PUNCTUATION)
        punctuation = raw_url[len(url):]
        # A strictly exact match can be retained. Any non-canonical spelling is
        # deliberately marked rather than risking another normalization error.
        if url in allowed:
            kept += 1
            rebuilt.append(raw_url)
        else:
            removed += 1
            rebuilt.append(UNVERIFIED_MARKER + punctuation)
        offset = end
        scan_at = end
    rebuilt.append(source[offset:])
    return "".join(rebuilt), CitationDiagnostics(
        detected=kept + removed,
        allowed=kept,
        removed=removed,
        reasons={_REASON_GUARD_ERROR: removed},
        error=type(exc).__name__,
    )
