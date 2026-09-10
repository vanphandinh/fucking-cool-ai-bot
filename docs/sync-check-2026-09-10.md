# Documentation sync check — 2026-09-10

## Scope

Synchronized repository documentation against the current runtime after:

- search cancellation hardening;
- `.env` permission hardening;
- current generic B.AI provider framework;
- current search, URL reading and Telegram flows.

A second pass re-checked the PR itself against `main`, canonical guides, runtime code, Docker/CI configuration and secret-file guardrails.

## Verified aligned

- AI provider docs match current generic registry/capability routing model.
- B.AI allowlist and vision capability (`max_images=1`) match the adapter.
- Vision docs match Telegram loader/application limits and capability-based routing.
- Telegram formatting docs match HTML sanitization, UTF-16 splitting and plain-text fallback behavior.
- Search docs match SearXNG -> DDGS bounded fallback, circuit breaker, cache and singleflight behavior.
- Crawl4AI docs match optional URL-rendering deployment and cancellation behavior.
- Deployment docs match private SearXNG architecture and current Compose profiles.
- CI documentation/check commands match the Python 3.11/3.12 audit workflow.

## Drift found on second pass and fixed

### Canonical env guide had not yet absorbed the hardening fix

`docs/ENV_SYNC.md` previously said atomic replacement preserved file mode, which was incomplete after the 2026-09-10 hardening. It now documents the actual behavior:

- new `.env` files use mode `0600`;
- existing `.env` files lose execute/group/other permission bits;
- permission-only repair does not rewrite file contents;
- stricter owner-only permissions are preserved.

The production workflow recommends checking the effective mode after sync.

### Env backup was documented but not ignored

Canonical docs recommend `cp .env .env.bak`, but `.gitignore` previously ignored only `.env`. That left a secret-bearing backup visible to Git and easy to stage accidentally.

Fixed guardrails:

- `.gitignore` now ignores `.env.bak` and `.env.*.bak`;
- `.dockerignore` excludes the same backup patterns from build context;
- env/deployment docs explicitly treat backups as secret files.

### HALF_OPEN cancellation semantics were only recorded in the audit snapshot

`docs/SEARCH_RESILIENCE.md` now records the runtime behavior directly:

- caller `CancelledError` propagates;
- cancellation is neutral for backend health;
- a cancelled HALF_OPEN probe releases `probe_in_flight` without incrementing failure count;
- circuit state remains HALF_OPEN so a later request can probe again.

### X fallback chain was stale in two canonical guides

`app/search/url_service.py` currently routes a failed specialized X read through optional Crawl4AI before the generic reader. `docs/X_CONTENT_FETCHING.md` and `DEPLOY_SEARXNG_VPS.md` previously skipped that stage in several diagrams/descriptions.

They now document the actual chain:

```text
public URL validation
 -> FxTwitter / X oEmbed for recognized X status URLs
 -> optional Crawl4AI
 -> generic reader
```

The X guide also lists `crawl4ai` as a valid `UrlReadResult.backend` and clarifies that `X_FETCH_ENABLED=0` disables only the specialized X resolver, not the common Crawl4AI/generic URL pipeline.

### SearXNG image pinning wording was tightened

Compose intentionally retains a mutable `latest` fallback when `SEARXNG_IMAGE` is unset. Canonical deployment/search docs now distinguish that setup fallback from the production recommendation to set a tested exact tag/digest through `SEARXNG_IMAGE`.

## Added documentation structure

- Added `docs/README.md` as documentation index.
- Defined canonical/current guides vs historical snapshots.
- Defined source-of-truth precedence for future drift resolution.

## Not changed intentionally

Historical plans/specs/audit/incident records remain snapshots. They should preserve the context of the implementation date rather than being rewritten as current guides.
