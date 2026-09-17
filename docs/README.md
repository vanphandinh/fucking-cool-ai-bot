# Documentation index

Tài liệu trong repo được chia thành **canonical/current guides** và một số audit/incident records không chứa superseded environment contracts. Khi có khác biệt, source code + tests + `.env.example` là source of truth cao nhất.

## Canonical / current guides

- [`../README.md`](../README.md) — kiến trúc tổng quan, cấu hình chính, quick start và verification.
- [`../DEPLOY_SEARXNG_VPS.md`](../DEPLOY_SEARXNG_VPS.md) — triển khai SearXNG private trên VPS.
- [`XKIRO.md`](XKIRO.md) — xKiro fallback adapter, live qualification, rollout và rollback.
- [`CHAINNODE.md`](CHAINNODE.md) — Chainnode primary text/vision provider, model pools và operational probes.
- [`telegram-vision-input.md`](telegram-vision-input.md) — Telegram image input, limits và capability-aware vision routing.
- [`TELEGRAM_FORMATTING.md`](TELEGRAM_FORMATTING.md) — Telegram-native HTML formatting, splitting và delivery fallback.
- [`QUESTION_CONTROLS.md`](QUESTION_CONTROLS.md) — renewable Telegram jobs, consent/Stop semantics, local bounds, rollout và rollback.
- [`SEARCH_RESILIENCE.md`](SEARCH_RESILIENCE.md) — search routing, circuit breaker, cache, singleflight và cancellation semantics.
- [`CRAWL4AI_INTEGRATION.md`](CRAWL4AI_INTEGRATION.md) — optional Crawl4AI URL rendering backend.
- [`X_CONTENT_FETCHING.md`](X_CONTENT_FETCHING.md) — direct X/Twitter status/thread fetching.
- [`ENV_SYNC.md`](ENV_SYNC.md) — strict `.env` synchronization, canonical provider contract và secret-file permissions.

## Retained records

- [`AUDIT_2026-09-10.md`](AUDIT_2026-09-10.md) — runtime hardening audit.
- [`SEARXNG_DDG_INCIDENT_2026-09-08.md`](SEARXNG_DDG_INCIDENT_2026-09-08.md) — search incident record.
- [`superpowers/plans/`](superpowers/plans/) — retained plans that do not preserve removed environment/provider contracts.
- [`superpowers/specs/`](superpowers/specs/) — retained durable design specs.

Git history is the archive for superseded environment/provider contracts; current tracked documentation must not teach those contracts.

## Source-of-truth order

1. source code + tests hiện tại;
2. `.env.example`, `docker-compose.yml`, `searxng/settings.example.yml` và CI workflow;
3. canonical/current guides ở trên;
4. retained audit/incident/design records.

## Current runtime snapshot

- Text provider order mặc định: `chainnode,xkiro`; Chainnode primary, xKiro fallback khi có credential + qualified model pool.
- Vision provider order mặc định: `chainnode,xkiro`; current vision targets advertise tối đa một image/request.
- Chainnode production defaults dùng `CHAINNODE_API_KEYS`, `CHAINNODE_TEXT_MODELS` và `CHAINNODE_VISION_MODELS`.
- xKiro runtime và qualification probe dùng canonical `XKIRO_API_KEYS`; runtime còn dùng `XKIRO_TEXT_MODELS`, `XKIRO_VISION_MODELS` và shared `XKIRO_BASE_URL`; runtime model IDs không hard-code trong source.
- Canonical retry fields là `PROVIDER_RETRY_MAX_CONSECUTIVE`, `PROVIDER_RETRY_MAX_PER_PROVIDER`, `PROVIDER_RETRY_MAX_PER_REQUEST`; recovery-hop field là `PROVIDER_RECOVERY_MAX_HOPS_PER_REQUEST`.
- `QUESTION_CONTROLS_ENABLED=0` là default rollback-safe; khi bật, renewable jobs vẫn giữ bounded leaf operations.
- `SEARCH_BACKEND=auto` ưu tiên SearXNG rồi fallback DDGS theo bounded resilience policy.
- Direct URL reading và search discovery là hai pipeline riêng; X/Twitter có specialized resolver, Crawl4AI là optional rendering backend trước generic reader.
- `.env.example` là complete supported key schema. `scripts/sync_env.py` không migrate/infer/rename/copy key values; unknown `.env` assignments làm sync exit `2` trước rewrite.
- Operational provider rollback là Chainnode-only bằng `TEXT_PROVIDER_ORDER=chainnode` và `VISION_PROVIDER_ORDER=chainnode`.

## Documentation sync checklist

Khi runtime thay đổi, kiểm tra ít nhất:

- provider registration, capabilities, route order, qualification và fallback semantics;
- canonical env keys/defaults/validation và secret handling;
- renewable-question interval, callback authorization, shutdown và rollback semantics;
- search timeouts, cache, circuit breaker, cancellation và singleflight behavior;
- URL/X/Crawl4AI ordering, SSRF boundary và rollback switches;
- Telegram trigger, vision limits, formatting, multipart delivery và memory behavior;
- Docker Compose profiles/images/mounts/healthchecks và VPS commands;
- CI/test commands và audit gates.
