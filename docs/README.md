# Documentation index

Tài liệu trong repo được chia thành hai nhóm: **canonical/current guides** dùng để vận hành code hiện tại và **historical/maintenance records** dùng để giữ bối cảnh thiết kế, incident và audit tại một thời điểm cụ thể.

Last full documentation sync: **2026-09-11**.

## Canonical / current guides

Các tài liệu dưới đây phải được giữ đồng bộ với source/runtime hiện tại:

- [`../README.md`](../README.md) — kiến trúc tổng quan, cấu hình chính, quick start và verification.
- [`../DEPLOY_SEARXNG_VPS.md`](../DEPLOY_SEARXNG_VPS.md) — triển khai SearXNG private trên VPS.
- [`BAI_INTEGRATION.md`](BAI_INTEGRATION.md) — B.AI adapter, model policy, provider routing, tools và health.
- [`CHAINNODE.md`](CHAINNODE.md) — optional Chainnode text provider, qualified model, configuration và rollback.
- [`telegram-vision-input.md`](telegram-vision-input.md) — Telegram image input, limits và capability-aware vision routing.
- [`TELEGRAM_FORMATTING.md`](TELEGRAM_FORMATTING.md) — Telegram-native HTML formatting, splitting và delivery fallback.
- [`SEARCH_RESILIENCE.md`](SEARCH_RESILIENCE.md) — search routing, circuit breaker, cache, singleflight và cancellation semantics.
- [`CRAWL4AI_INTEGRATION.md`](CRAWL4AI_INTEGRATION.md) — optional Crawl4AI URL rendering backend.
- [`X_CONTENT_FETCHING.md`](X_CONTENT_FETCHING.md) — direct X/Twitter status/thread fetching.
- [`ENV_SYNC.md`](ENV_SYNC.md) — đồng bộ `.env` theo `.env.example` và secret-file permissions.

## Historical / maintenance records

Các file này là snapshot/record theo thời điểm và **không nên được rewrite chỉ để phản ánh runtime mới**:

- [`sync-check-2026-09-10.md`](sync-check-2026-09-10.md) — documentation sync + second-pass audit record.
- [`AUDIT_2026-09-10.md`](AUDIT_2026-09-10.md) — runtime hardening audit ngày 2026-09-10.
- [`SEARXNG_DDG_INCIDENT_2026-09-08.md`](SEARXNG_DDG_INCIDENT_2026-09-08.md) — incident record.
- [`superpowers/plans/`](superpowers/plans/) — implementation plans lịch sử.
- [`superpowers/specs/`](superpowers/specs/) — design specs lịch sử.

Historical docs có thể nhắc tới provider, implementation hoặc constraint đã thay đổi sau đó. Khi có xung đột, ưu tiên canonical/current guides và source code hiện tại.

## Source-of-truth order

Khi tài liệu và implementation không khớp, dùng thứ tự ưu tiên sau:

1. source code + tests hiện tại;
2. `.env.example`, `docker-compose.yml`, `searxng/settings.example.yml` và CI workflow;
3. canonical/current guides ở trên;
4. audit/incident/plan/spec/sync records lịch sử.

## Current runtime snapshot

- Text provider registry register B.AI và optional Chainnode; default `TEXT_PROVIDER_ORDER=bai` nên Chainnode chỉ active khi deployment cấu hình và opt in qua order.
- Vision provider hiện là B.AI; default `VISION_PROVIDER_ORDER=bai`, B.AI vision slot advertise `max_images=1`.
- AI OpenAI-compatible transport dùng phase timeouts `connect=8s`, `write=20s`, `pool=5s`; `BAI_REQUEST_TIMEOUT_SEC` và `CHAINNODE_REQUEST_TIMEOUT_SEC` điều khiển read timeout, default `60s`.
- `SEARCH_BACKEND=auto` ưu tiên SearXNG rồi fallback DDGS theo bounded resilience policy.
- Direct URL reading và search discovery là hai pipeline riêng; X/Twitter có specialized resolver, Crawl4AI là optional rendering backend trước generic reader.
- `sync_env.py` giữ value của key còn tồn tại, đồng bộ key/comment/order theo `.env.example`, và bảo đảm `.env` không còn group/other/execute permissions; file mới dùng mode `0600`.
- Vì sync giữ existing values, deployment cũ có AI request timeout `30.0` phải đổi thủ công thành `60.0` nếu muốn áp dụng read timeout mới.
- `.env.bak` / `.env.*.bak` được ignore khỏi Git và Docker build context nhưng vẫn phải được xử lý như secret production.
- Caller cancellation không được tính thành backend failure. Nếu cancellation xảy ra trong HALF_OPEN search probe, probe slot được release để request sau còn có thể probe lại.

## Documentation sync checklist

Khi runtime thay đổi, kiểm tra ít nhất:

- provider registration, model allowlist, capabilities, route order và fallback semantics;
- env keys/defaults/validation, migration behavior và secret handling;
- search timeouts, cache, circuit breaker, cancellation và singleflight behavior;
- URL/X/Crawl4AI ordering, SSRF boundary và rollback switches;
- Telegram trigger, vision limits, formatting, multipart delivery và memory behavior;
- Docker Compose profiles/images/mounts/healthchecks và VPS commands;
- CI/test commands và audit gates.

Không tạo documentation diff chỉ để thay đổi wording nếu behavior vẫn đúng. Mục tiêu của sync là sửa drift thực tế và làm rõ source of truth.
