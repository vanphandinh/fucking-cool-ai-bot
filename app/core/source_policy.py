"""Source URL canonicalization and diversity selection policy."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

import tldextract

_TRACKING_QUERY_KEYS = frozenset(
    {
        "_ga",
        "_gl",
        "dclid",
        "fbclid",
        "gbraid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "msclkid",
        "twclid",
        "wbraid",
        "yclid",
    }
)
_TLD_EXTRACT = tldextract.TLDExtract(
    suffix_list_urls=(),
    include_psl_private_domains=True,
)
_X_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_X_RESERVED_ROUTES = frozenset(
    {
        "account",
        "compose",
        "explore",
        "hashtag",
        "home",
        "i",
        "intent",
        "jobs",
        "login",
        "messages",
        "notifications",
        "privacy",
        "search",
        "settings",
        "share",
        "signup",
        "tos",
    }
)
_FACEBOOK_RESERVED_ROUTES = frozenset(
    {
        "ajax",
        "business",
        "dialog",
        "events",
        "gaming",
        "help",
        "home.php",
        "login",
        "marketplace",
        "messages",
        "pages",
        "people",
        "photo",
        "photos",
        "plugins",
        "privacy",
        "reel",
        "reels",
        "search",
        "settings",
        "share",
        "video",
        "videos",
        "watch",
    }
)
_FACEBOOK_GROUP_RESERVED_ROUTES = frozenset({"create", "discover", "feed", "join"})
_GITHUB_RESERVED_ROUTES = frozenset(
    {
        "about",
        "apps",
        "codespaces",
        "collections",
        "contact",
        "customer-stories",
        "enterprise",
        "events",
        "explore",
        "features",
        "issues",
        "login",
        "marketplace",
        "new",
        "notifications",
        "orgs",
        "pricing",
        "pulls",
        "search",
        "security",
        "settings",
        "site",
        "sponsors",
        "topics",
        "trending",
        "users",
    }
)


@dataclass(frozen=True)
class SourceIdentity:
    canonical_url: str
    site_key: str
    identity_key: str
    bucket_key: str
    bucket_limit: int


@dataclass(frozen=True)
class PlatformAdapter:
    name: str
    site_keys: frozenset[str]
    extractor: Callable[[SplitResult], str | None]
    bucket_limit: int = 2

    def matches(self, site_key: str) -> bool:
        return site_key in self.site_keys


def _normalized_host(host: str) -> tuple[str, bool]:
    """Normalize a hostname for comparison and return whether it is IPv6."""
    host = host.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            return host.encode("idna").decode("ascii"), False
        except UnicodeError:
            return "", False
    return address.compressed, address.version == 6


def _is_tracking_query_key(key: str) -> bool:
    key = key.lower()
    return key.startswith("utm_") or key in _TRACKING_QUERY_KEYS


def canonicalize_source_url(value: object) -> str:
    """Normalize a source URL with generic, site-independent rules."""
    raw_url = str(value or "").strip()
    if not raw_url:
        return ""
    try:
        parsed = urlsplit(raw_url)
        scheme = parsed.scheme.lower()
        host, is_ipv6 = _normalized_host(parsed.hostname or "")
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if scheme not in {"http", "https"} or not host:
        return ""

    netloc = f"[{host}]" if is_ipv6 else host
    is_default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    if port is not None and not is_default_port:
        netloc = f"{netloc}:{port}"

    path = parsed.path.rstrip("/")
    query_pairs = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_query_key(key)
    ]
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def source_site_key(url: str) -> str:
    """Return the registrable domain/eTLD+1 for source grouping."""
    canonical = canonicalize_source_url(url)
    if not canonical:
        return ""
    try:
        host = urlsplit(canonical).hostname or ""
    except ValueError:
        return ""
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        extracted = _TLD_EXTRACT(host)
        return extracted.top_domain_under_public_suffix or host
    return host


def _path_segments(parsed: SplitResult) -> list[str]:
    return [segment for segment in parsed.path.split("/") if segment]


def _query_value(parsed: SplitResult, key: str) -> str:
    for query_key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if query_key.lower() == key:
            return value.strip()
    return ""


def _extract_x_publisher(parsed: SplitResult) -> str | None:
    segments = _path_segments(parsed)
    if not segments:
        return None
    handle = segments[0]
    if handle.lower() in _X_RESERVED_ROUTES or not _X_HANDLE_RE.fullmatch(handle):
        return None
    return handle.lower()


def _extract_facebook_publisher(parsed: SplitResult) -> str | None:
    segments = _path_segments(parsed)
    if not segments:
        return None

    first = segments[0].lower()
    if first in {"profile.php", "story.php", "permalink.php"}:
        publisher_id = _query_value(parsed, "id")
        return f"id:{publisher_id}" if publisher_id.isdigit() else None
    if first.endswith(".php"):
        return None

    if first == "groups":
        if len(segments) < 2:
            return None
        group = segments[1].lower()
        if not group or group in _FACEBOOK_GROUP_RESERVED_ROUTES:
            return None
        return f"group:{group}"

    if first in _FACEBOOK_RESERVED_ROUTES:
        return None
    if first.isdigit():
        return f"id:{first}"
    return f"slug:{first}"


def _extract_github_publisher(parsed: SplitResult) -> str | None:
    segments = _path_segments(parsed)
    if not segments:
        return None
    owner = segments[0].lower()
    if owner in _GITHUB_RESERVED_ROUTES:
        return None
    return owner


_PLATFORM_ADAPTERS = (
    PlatformAdapter(
        name="x",
        site_keys=frozenset({"x.com", "twitter.com", "fxtwitter.com", "fixupx.com"}),
        extractor=_extract_x_publisher,
    ),
    PlatformAdapter(
        name="facebook",
        site_keys=frozenset({"facebook.com"}),
        extractor=_extract_facebook_publisher,
    ),
    PlatformAdapter(
        name="github",
        site_keys=frozenset({"github.com"}),
        extractor=_extract_github_publisher,
    ),
)


def classify_source(value: object) -> SourceIdentity | None:
    """Classify a source into identity and diversity bucket."""
    canonical_url = canonicalize_source_url(value)
    if not canonical_url:
        return None
    site_key = source_site_key(canonical_url)
    if not site_key:
        return None

    parsed = urlsplit(canonical_url)
    for adapter in _PLATFORM_ADAPTERS:
        if not adapter.matches(site_key):
            continue
        publisher = adapter.extractor(parsed)
        identity_key = (
            f"publisher:{adapter.name}:{publisher}" if publisher else f"url:{canonical_url}"
        )
        return SourceIdentity(
            canonical_url=canonical_url,
            site_key=site_key,
            identity_key=identity_key,
            bucket_key=f"platform:{adapter.name}",
            bucket_limit=adapter.bucket_limit,
        )

    key = f"site:{site_key}"
    return SourceIdentity(
        canonical_url=canonical_url,
        site_key=site_key,
        identity_key=key,
        bucket_key=key,
        bucket_limit=1,
    )


def select_diverse_sources(sources: list[dict], limit: int) -> list[dict[str, str]]:
    """Select sources by identity and diversity bucket while preserving input order."""
    if limit <= 0:
        return []

    seen_urls: set[str] = set()
    seen_identities: set[str] = set()
    bucket_counts: dict[str, int] = {}
    out: list[dict[str, str]] = []

    for src in sources:
        if not isinstance(src, dict):
            continue
        identity = classify_source(src.get("url"))
        if identity is None:
            continue
        if identity.canonical_url in seen_urls:
            continue
        if identity.identity_key in seen_identities:
            continue
        if bucket_counts.get(identity.bucket_key, 0) >= identity.bucket_limit:
            continue

        seen_urls.add(identity.canonical_url)
        seen_identities.add(identity.identity_key)
        bucket_counts[identity.bucket_key] = bucket_counts.get(identity.bucket_key, 0) + 1
        out.append(
            {
                "title": str(src.get("title") or ""),
                "url": identity.canonical_url,
                "snippet": str(src.get("snippet") or ""),
            }
        )
        if len(out) >= limit:
            break

    return out
