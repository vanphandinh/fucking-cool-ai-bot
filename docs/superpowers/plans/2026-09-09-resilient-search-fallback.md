# Resilient Search Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make web/image search self-recovering under SearXNG/DDGS timeout, rate-limit, partial-result, and outage conditions without unbounded retry.

**Architecture:** `SEARCH_BACKEND=auto` uses SearXNG as primary, DDGS as one-shot fallback, then stale cache. A per-backend/per-kind circuit breaker controls future requests (`CLOSED -> OPEN -> HALF_OPEN`), while a total deadline, TTL cache, and singleflight bound latency and upstream load. Explicit `searxng`/`ddgs` modes remain strict.

**Tech Stack:** Python 3.11+, asyncio, httpx, ddgs, Pydantic Settings, SearXNG, unittest, Ruff, Docker Compose.

**Spec:** `docs/SEARCH_RESILIENCE.md`

## Global Constraints

- No ping-pong retry in one request; each backend is attempted at most once.
- Search deadlines are independent from AI provider timeouts.
- Web partial results are preserved and may be topped up; image search avoids unnecessary top-up.
- Errors, timeouts, and empty responses are never cached.
- Cancellation propagates.
- Production SearXNG stays near latest but promotes only a tested build.
- Completion requires post-implementation audit/debug loops until Critical=0 and High=0.

---

### Task 1: Search-specific timeout budget

**Files:** `app/config.py`, `.env.example`, `app/search/searxng_backend.py`, `app/search/ddgs_backend.py`, `tests/test_search_resilience.py`

- [x] Add `SEARXNG_TIMEOUT_SEC=7`, `DDGS_TIMEOUT_SEC=8`, `SEARCH_TOTAL_TIMEOUT_SEC=15`.
- [x] Validate backend timeout <= total search timeout <= question timeout.
- [x] Stop using generic `REQUEST_TIMEOUT_SEC` for search.
- [x] Add regression tests.

### Task 2: Circuit breaker primitives

**Files:** `app/search/resilience.py`, `tests/test_search_resilience.py`

- [x] Add backend/kind keyed CLOSED/OPEN/HALF_OPEN state.
- [x] Open after repeated failures; open immediately for rate-limit.
- [x] Permit one HALF_OPEN probe only.
- [x] Reset on successful probe.

### Task 3: Bounded resilient router

**Files:** `app/search/router.py`, `app/search/service.py`, `app/search/image_service.py`

- [x] Add absolute total deadline.
- [x] Track attempted backends to forbid ping-pong retry.
- [x] Route SearXNG -> DDGS -> stale cache -> SearchError.
- [x] Preserve strict single-backend modes.

### Task 4: Partial result preservation/top-up

**Files:** `app/search/router.py`, `tests/test_search_resilience.py`, `tests/test_search_backend_auto.py`

- [x] Web: return immediately with >=2 usable SearXNG results.
- [x] Web: with 1 result, keep it and DDGS top-up.
- [x] Image: 1 result is sufficient; avoid unnecessary DDGS traffic.
- [x] Canonical URL dedupe and result-limit enforcement.

### Task 5: Cache and singleflight

**Files:** `app/search/resilience.py`, `app/search/runtime.py`, `tests/test_search_resilience.py`

- [x] Add bounded LRU fresh/stale cache.
- [x] Fresh TTL: web 60s, image 120s; stale window 900s.
- [x] Add per-key singleflight coalescing.
- [x] Never cache empty/error responses.

### Task 6: Shared SearXNG HTTP client

**Files:** `app/search/runtime.py`, `app/search/searxng_backend.py`, `app/main.py`

- [x] Reuse one httpx AsyncClient per Settings runtime.
- [x] Use SearXNG-specific client timeout.
- [x] Close runtimes during bot shutdown.

### Task 7: Harden SearXNG engine policy

**Files:** `searxng/settings.example.yml`, `tests/test_searxng_config.py`

- [x] Global outgoing timeout 3s / max 5s.
- [x] Google CSE web/image override 5s.
- [x] 429 suspension 3600s; access denied/CAPTCHA longer.
- [x] Preserve private JSON API and known unstable engine removals.

### Task 8: Documentation

**Files:** `.env.example`, `docs/SEARCH_RESILIENCE.md`

- [x] Document config, fallback order, circuit recovery, cache/singleflight.
- [x] Document stay-near-latest SearXNG promotion policy.
- [x] Document post-deployment failure-injection/audit loop.

### Task 9: Verification and audit/debug loop

**Files:** tests as needed for every discovered regression.

- [ ] Run Ruff, compile, YAML validation, full offline regression suite, unittest discovery, dependency audit, Docker build on Python 3.11/3.12 CI.
- [ ] Audit cancellation, deadline, circuit recovery, stale cache, singleflight cleanup, connection cleanup, malformed responses/URLs, strict-mode behavior.
- [ ] For each Critical/High finding, first add/identify a regression test, fix root cause, run targeted/full CI, then audit again.
- [ ] Repeat until Critical=0 and High=0.

### Task 10: Pull request

- [ ] Review branch diff against `main` for dead code, duplicated routing, hidden retry, timeout mismatch, and docs/config drift.
- [ ] Verify final CI is green at branch head.
- [ ] Open PR from `feat/resilient-search-fallback` to `main` with implementation summary, test evidence, rollout notes, and remaining non-blocking risks.
