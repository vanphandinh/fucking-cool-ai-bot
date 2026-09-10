# Documentation sync check — 2026-09-10

## Scope

Synchronized repository documentation against the current runtime after:

- search cancellation hardening;
- `.env` permission hardening;
- current generic B.AI provider framework;
- current search, URL reading and Telegram flows.

## Verified aligned

- AI provider docs match current generic registry/capability routing model.
- Vision docs match capability-based image limits.
- Search docs match SearXNG -> DDGS bounded fallback, circuit breaker and cancellation behavior.
- Crawl4AI docs match optional URL-rendering deployment.
- X/Twitter docs match specialized fetch flow before generic URL reading.
- Deployment docs match private SearXNG architecture.

## Updated

- Added `docs/README.md` as documentation index.
- Documented canonical vs historical documentation boundary.
- Documented current `.env` permission behavior:
  - new `.env` files use `0600`;
  - existing `.env` files have group/other/execute bits removed without rewriting content when only permissions drift.
- Documented cancellation behavior:
  - caller cancellation is propagated;
  - HALF_OPEN probe slots are released without recording backend failure.

## Not changed intentionally

Historical plans/specs/audit records remain snapshots. They should preserve the context of the implementation date rather than being rewritten as current guides.
