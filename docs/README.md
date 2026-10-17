# Documentation index

Tài liệu trong repo chỉ mô tả **trạng thái vận hành hiện tại**. Audit, incident, migration record, implementation plan và design cũ được lưu trong Git history thay vì giữ trong working tree. Các tài liệu quy trình/session của Web Evidence V2 (2026-10-14 → 2026-10-16) đã được dọn khỏi working tree theo đúng nguyên tắc này.

Khi có khác biệt, áp dụng thứ tự source-of-truth bên dưới; source code + tests hiện tại đứng cao nhất.

## Current guides

- [`../README.md`](../README.md) — kiến trúc tổng quan, cấu hình chính, quick start và verification.
- [`../DEPLOY_SEARXNG_VPS.md`](../DEPLOY_SEARXNG_VPS.md) — triển khai SearXNG private trên VPS.
- [`XKIRO.md`](XKIRO.md) — xKiro declarative provider profile, live qualification, rollout và rollback.
- [`CHAINNODE.md`](CHAINNODE.md) — Chainnode declarative primary profile, model pools và operational probes.
- [`telegram-vision-input.md`](telegram-vision-input.md) — Telegram image input, limits và capability-aware vision routing.
- [`TELEGRAM_FORMATTING.md`](TELEGRAM_FORMATTING.md) — Telegram-native HTML formatting, splitting và delivery fallback.
- [`QUESTION_CONTROLS.md`](QUESTION_CONTROLS.md) — renewable Telegram jobs, consent/Stop semantics, local bounds, rollout và rollback.
- [`SEARCH_RESILIENCE.md`](SEARCH_RESILIENCE.md) — search routing, circuit breaker, cache, singleflight và cancellation semantics.
- [`CRAWL4AI_INTEGRATION.md`](CRAWL4AI_INTEGRATION.md) — optional Crawl4AI URL rendering backend, gated production activation and rollback runbook.
- [`X_CONTENT_FETCHING.md`](X_CONTENT_FETCHING.md) — direct X/Twitter status/thread fetching.
- [`ENV_SYNC.md`](ENV_SYNC.md) — strict `.env` synchronization, canonical provider contract và secret-file permissions.
- [`VPS_REDEPLOY_AND_FEATURE_FLAGS.md`](VPS_REDEPLOY_AND_FEATURE_FLAGS.md) — chạy lại toàn bộ gate trên VPS, redeploy và các điểm kiểm tra sau cutover.
- [`WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md`](WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md) — bộ 21 câu live smoke V2 và cách chấm (`scripts/web_evidence_live_smoke.py` đọc trực tiếp file này).
- [`benchmarks/README.md`](benchmarks/README.md) — offline replay, fixture và giới hạn của các proxy metric.

## Acceptance record

Chỉ giữ lại bản ghi quyết định; số liệu chi tiết và artifact tái lập nằm trong Git history.

- [`acceptance/web_evidence_v2_independent_validation_20261016/RELEASE_DECISION.md`](acceptance/web_evidence_v2_independent_validation_20261016/RELEASE_DECISION.md) — 15 tiêu chí, verdict và điều kiện bật pilot.
- [`acceptance/web_evidence_v2_independent_validation_20261016/QUALITY_EVALUATION.md`](acceptance/web_evidence_v2_independent_validation_20261016/QUALITY_EVALUATION.md) — chấm 74 lượt model thật (V1 36/37, V2 35/37, Q6 sai 3/3).
- [`acceptance/web_evidence_v2_freshness_20261016/FRESHNESS_DIAGNOSIS.md`](acceptance/web_evidence_v2_freshness_20261016/FRESHNESS_DIAGNOSIS.md) — nguyên nhân retrieval của lỗi Q6 (H3/H5).

## Source-of-truth order

1. source code + tests hiện tại;
2. `.env.example`, `docker-compose.yml`, `searxng/settings.example.yml` và CI workflow;
3. `README.md` và current operator guides ở trên;
4. Git history cho audit, incident, migration record, plan và design đã hoàn thành.

Active documentation không được dạy superseded environment/provider contracts.

## Current runtime snapshot

- Text provider order mặc định: `chainnode,xkiro`; Chainnode primary, xKiro fallback khi có credential + qualified model pool.
- Vision provider order mặc định: `chainnode,xkiro`; current vision targets advertise tối đa một image/request.
- Chainnode production defaults dùng `AI_PROVIDERS__CHAINNODE__API_KEYS`, `AI_PROVIDERS__CHAINNODE__TEXT_MODELS` và `AI_PROVIDERS__CHAINNODE__VISION_MODELS`.
- xKiro runtime và qualification probe dùng canonical `AI_PROVIDERS__XKIRO__API_KEYS`; runtime còn dùng `AI_PROVIDERS__XKIRO__TEXT_MODELS`, `AI_PROVIDERS__XKIRO__VISION_MODELS` và shared `AI_PROVIDERS__XKIRO__BASE_URL`; runtime model IDs không hard-code trong source.
- Canonical retry fields là `PROVIDER_RETRY_MAX_CONSECUTIVE`, `PROVIDER_RETRY_MAX_PER_PROVIDER`, `PROVIDER_RETRY_MAX_PER_REQUEST`; recovery-hop field là `PROVIDER_RECOVERY_MAX_HOPS_PER_REQUEST`.
- `QUESTION_CONTROLS_ENABLED=0` là default rollback-safe; khi bật, renewable jobs vẫn giữ bounded leaf operations.
- Controlled multipart delivery có terminal `PARTIALLY_DELIVERED` khi Telegram xác nhận một số phần, nhưng chưa đủ; lưu prefix có đánh dấu và chỉ đếm partial metric. Không có auto-replay.
- Conversation memory là in-memory, tối đa `MAX_CONTEXT_CONVERSATIONS=2048` keys (LRU), TTL từ truy cập cuối `CONTEXT_TTL_SEC=86400`; rate-limit records được dọn dần theo thời gian bằng monotonic clock.
- `SEARCH_BACKEND=auto` ưu tiên SearXNG rồi fallback DDGS theo bounded resilience policy. Web result luôn được dedupe canonical, xếp hạng lexical (title×2/snippet×1), đa dạng domain, và SearXNG chỉ được chấp nhận sớm khi có >= 2 result từ 2 domain độc lập đạt score >= 0,20; cache web dùng namespace `:v2`.
- Auto-fetch luôn bật: tự đọc tối đa 2 nguồn xếp hạng cao nhất, song song <= 2, budget `WEB_AUTO_FETCH_BUDGET_SEC` (<= 20s) dùng chung cả câu hỏi và bị chặn bởi budget question/job; bỏ qua khi user đã đưa URL; nguồn lỗi được báo đúng trạng thái.
- Fresh Synthesis luôn đóng gói chứng cứ web theo relevance + đa dạng nguồn, giữ URL/chunk label; output của tool không thuộc web giữ thứ tự và ngân sách riêng. Packer V2 và instruction grounding là duy nhất; không còn `synthesis_mode` hay packer equal-split V1. Cap không đổi: 6.000 ký tự/tool và 12.000 ký tự/synthesis.
- Auto-fetch dùng một coordinator request-scoped cho cả câu hỏi: deadline monotonic, reservation canonical URL bằng lock (tổng <= 2 nguồn dù có nhiều `web_search` đồng thời) và semaphore 2 lượt đọc song song. Delimiter hệ thống trong nội dung không đáng tin bị vô hiệu hóa (chống prompt injection/đóng khung giả).
- Direct URL reading và search discovery là hai pipeline riêng; X/Twitter có specialized resolver, Crawl4AI là optional rendering backend trước generic reader.
- `.env.example` là complete supported key schema. `scripts/sync_env.py` không migrate/infer/rename/copy key values; unknown `.env` assignments làm sync exit `2` trước rewrite. Ba key đã retire — `WEB_EVIDENCE_V2_ENABLED`, `WEB_EVIDENCE_V2_GROUP_IDS`, `WEB_AUTO_FETCH_ENABLED` — phải bị xóa khỏi `.env` trước khi sync.
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
