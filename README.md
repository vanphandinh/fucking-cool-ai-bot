# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.

**Default AI route: Chainnode primary -> xKiro fallback.** Text và vision dùng cùng capability-aware, health-aware multi-provider router. Bot vẫn hỗ trợ web search, image search, direct URL reading, X/Twitter reader, Crawl4AI, vision input, renewable question controls và Telegram-native HTML formatting.

## Tài liệu chính

- [Chainnode primary provider](docs/CHAINNODE.md)
- [xKiro fallback provider](docs/XKIRO.md)
- [Đồng bộ `.env`](docs/ENV_SYNC.md)
- [Telegram vision input](docs/telegram-vision-input.md)
- [Telegram-native formatting](docs/TELEGRAM_FORMATTING.md)
- [Renewable question controls](docs/QUESTION_CONTROLS.md)
- [Search resilience](docs/SEARCH_RESILIENCE.md)
- [Crawl4AI URL reading](docs/CRAWL4AI_INTEGRATION.md)
- [X/Twitter content fetching](docs/X_CONTENT_FETCHING.md)
- [SearXNG production trên VPS](DEPLOY_SEARXNG_VPS.md)
- [Chạy lại gate và bật cờ trên VPS](docs/VPS_REDEPLOY_AND_FEATURE_FLAGS.md)
- [Bộ câu live smoke V1/V2](docs/WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md)

`README.md`, `.env.example`, runtime source, Docker/CI và các active operator guide là source of truth cho current deployment contract.

Hội thoại giữ tối đa `MAX_CONTEXT_CONVERSATIONS=2048` chat/topic trong RAM (LRU) và hết hạn sau `CONTEXT_TTL_SEC=86400` giây không truy cập; giới hạn lượt mỗi key vẫn theo `MAX_CONTEXT_TURNS`. Với controlled multipart trả lời, Telegram xác nhận 1..N-1 phần sẽ kết thúc `PARTIALLY_DELIVERED` và lưu prefix được đánh dấu, không tự động gửi lại.

## AI architecture

```text
Telegram request
   │
   ▼
Orchestrator + tools/search/url readers
   │
   ▼
AIProviderRouter
   │
   ├─ TEXT_PROVIDER_ORDER
   ├─ VISION_PROVIDER_ORDER
   ├─ capability filtering
   ├─ scoped health/cooldown filtering
   └─ bounded retry + recovery scheduler
          │
          ├─ Chainnode target pool
          │    ├─ text model × credential targets
          │    └─ vision model × credential targets
          └─ xKiro target pool
               ├─ text model × credential targets
               └─ vision model × credential targets
```

Chainnode và xKiro là declarative `ProviderProfile` entries trong `config/ai-providers.toml`; cả hai dùng `openai-chat` driver. Concrete targets được mở rộng theo deterministic **model-major × credential** order. Provider OpenAI-compatible mới chỉ cần catalog + `AI_PROVIDERS__<ID>__*`, không cần module Python theo vendor.

### Thêm OpenAI-compatible provider chỉ bằng config/catalog

1. Thêm một `ProviderProfile` mới vào `config/ai-providers.toml` với `driver = "openai-chat"`, endpoint mặc định, route capabilities và recovery overrides nếu cần.
2. Cấu hình runtime bằng `AI_PROVIDERS__<ID>__API_KEYS`, `BASE_URL`, `TEXT_MODELS`, `VISION_MODELS` và `REQUEST_TIMEOUT_SEC` tương ứng.
3. Thêm provider id vào `TEXT_PROVIDER_ORDER` và/hoặc `VISION_PROVIDER_ORDER`.

Không cần thêm module Python theo vendor và không sửa router, retry, health hoặc orchestrator.

### Thêm native protocol bằng driver mới

1. Implement driver mới dưới `app/ai/drivers/` để serialize `ChatRequest`, thực hiện đúng một network attempt và parse về `ChatResponse`.
2. Đăng ký driver id trong `app/ai/drivers/registry.py`.
3. Thêm `ProviderProfile` vào `config/ai-providers.toml` trỏ tới driver mới và cấu hình `AI_PROVIDERS__<ID>__*` như bình thường.

Native protocol mới không yêu cầu thay đổi `AIProviderRouter`, retry, scoped health, target rotation, tool orchestration hay fallback logic.

## Canonical provider and retry configuration

Current runtime/environment contract chỉ dùng canonical plural pools và canonical retry fields:

```env
AI_PROVIDERS__CHAINNODE__API_KEYS=
AI_PROVIDERS__CHAINNODE__BASE_URL=https://dn.chainno.de/v1
AI_PROVIDERS__CHAINNODE__TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash
AI_PROVIDERS__CHAINNODE__VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
AI_PROVIDERS__CHAINNODE__REQUEST_TIMEOUT_SEC=60.0

AI_PROVIDERS__XKIRO__API_KEYS=
AI_PROVIDERS__XKIRO__BASE_URL=https://api.xkiro.com/v1
AI_PROVIDERS__XKIRO__TEXT_MODELS=
AI_PROVIDERS__XKIRO__VISION_MODELS=
AI_PROVIDERS__XKIRO__REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro

PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5
PROVIDER_RECOVERY_MAX_HOPS_PER_REQUEST=5
```

Một phần tử trong plural pool là hợp lệ. Chainnode text/vision pools có qualified defaults; xKiro model pools cố ý để trống cho tới khi current candidate pass live qualification. Runtime không query model catalog khi startup hoặc mỗi request.

`AI_PROVIDERS__XKIRO__BASE_URL` dùng chung current endpoint contract giữa runtime và retained qualification probe, default `https://api.xkiro.com/v1`.

## Strict `.env` synchronization

`.env.example` là complete supported key schema. Chạy:

```bash
python scripts/sync_env.py
```

Synchronizer giữ value hiện có chỉ cho keys có trong `.env.example`, thêm missing canonical keys từ template defaults, và fail với exit code `2` nếu `.env` có unknown assignment. Khi fail, `.env` không bị rewrite. Synchronizer không migrate, infer, rename hoặc copy values giữa keys. Lần chạy thành công thứ hai là idempotent.

Trước khi deploy canonical-only revision, remove mọi assignment không có trong deployed `.env.example`; xem [docs/ENV_SYNC.md](docs/ENV_SYNC.md) cho deployment sequence đầy đủ.

## xKiro live qualification

`GET <AI_PROVIDERS__XKIRO__BASE_URL>/models` là source of truth cho candidate hiện tại. Không hard-code xKiro runtime model IDs trong source.

Text candidate phải pass:

```text
exact model ID exists
access_tier == free
pricing.input == 0
pricing.output == 0
capabilities.tools == true
plain non-stream chat
structured tool call
tool continuation
```

Vision candidate còn phải pass `capabilities.vision == true`, known-image understanding và vision/tool flow.

Probe dùng canonical credential pool và lấy credential non-blank đầu tiên cho single-credential qualification run:

```bash
export AI_PROVIDERS__XKIRO__API_KEYS='...'
export AI_PROVIDERS__XKIRO__BASE_URL='https://api.xkiro.com/v1'
python scripts/probe_xkiro.py \
  --text-model '<candidate-text-id>' \
  --vision-model '<candidate-vision-id>' \
  --image './known-test-image.png'
```

Sau khi candidate pass, cấu hình IDs vào `AI_PROVIDERS__XKIRO__TEXT_MODELS` / `AI_PROVIDERS__XKIRO__VISION_MODELS`; runtime dùng cùng canonical `AI_PROVIDERS__XKIRO__API_KEYS` pool.

## Retry, recovery and tool state

Shared adapter thực hiện một HTTP attempt cho mỗi `chat()` call; router quyết định bounded retry/rotation. Current behavior gồm:

- `ConnectError` / `ConnectTimeout`: bounded exact-target retry rồi rotate khi cần;
- `ReadTimeout`: ưu tiên eligible alternate provider, nếu không có thì bounded exact-target retry;
- resource failures dùng credential/model/entitlement health scopes theo provider;
- cumulative retry và recovery-hop budgets là request-scoped;
- completed tool calls không được chạy lại khi target/provider thay đổi;
- provider-local metadata không được carry sang target khác;
- khi tool budget đóng, Fresh Synthesis dùng provider-neutral evidence và không gửi tool schema.

Retry bounds validation:

- consecutive: `1..5`;
- per-provider cumulative: `1..10` và `>= consecutive`;
- per-request cumulative: `1..20` và `>= consecutive`;
- recovery hops: `1..20`.

## Web Evidence V2 (flag-gated)

Pipeline evidence V2 nằm sau feature flag và **mặc định tắt**:

```env
WEB_EVIDENCE_V2_ENABLED=false
WEB_EVIDENCE_MAX_EXTRACTED_CHARS=120000
WEB_AUTO_FETCH_ENABLED=false
WEB_AUTO_FETCH_MAX_SOURCES=2
WEB_AUTO_FETCH_BUDGET_SEC=20
```

Khi bật, V2 thay đổi bốn điểm:

1. **Evidence extraction:** nội dung trang được parse bounded, bỏ chrome/navigation, chọn chunk liên quan và giữ label `[SOURCE n / CHUNK m]` + URL thay vì cắt 8.000 ký tự đầu tiên.
2. **Source ranking:** kết quả web được dedupe theo canonical URL và xếp theo lexical relevance (title trọng số 2, snippet 1) kèm đa dạng domain; `SEARCH_BACKEND=auto` chỉ chấp nhận SearXNG sớm khi có >= 2 result từ 2 domain độc lập đạt score >= 0,20.
3. **Conditional auto-fetch:** chỉ khi cả hai flag V2 bật; tối đa 2 nguồn **cho toàn câu hỏi** nhờ một coordinator request-scoped (deadline monotonic + reservation canonical URL bằng lock + semaphore 2 lượt đọc song song), nên nhiều `web_search` đồng thời không thể vượt cap; tổng budget 20 giây cho cả câu hỏi và bị chặn tiếp bởi budget question/job; ưu tiên URL người dùng, cache theo canonical URL trong request, nguồn không đọc được được báo đúng trạng thái.
4. **Synthesis packing:** bằng chứng web được đóng gói theo độ liên quan và đa dạng nguồn, giữ URL/chunk label, tổng vẫn **<= 12.000 ký tự**; output của tool không thuộc web không bị bỏ. `synthesis_mode` là request-scoped và **mặc định `v1`** (chia đều như trước PR4); `v2` mới dùng packing thích ứng. Search listing bị giới hạn <= 3.600 ký tự khi auto-fetch để dành tối thiểu 2.400 ký tự cho bằng chứng.
5. **Framing hardening:** delimiter hệ thống trong nội dung không đáng tin bị vô hiệu hóa, nên trang độc hại không thể tạo marker đóng/mở khung giả hoặc nhãn nguồn giả.

Rollback tức thời: đặt `WEB_EVIDENCE_V2_ENABLED=0` (và/hoặc `WEB_AUTO_FETCH_ENABLED=0`) rồi recreate bot. Không thay đổi image search, X/Twitter resolver, giới hạn tải **2 MiB**, cap **6.000 ký tự/tool**, cap **12.000 ký tự/synthesis**, SSRF/DNS pinning/redirect validation, timeout/cancellation, cache/circuit breaker và Crawl4AI isolation.

Benchmark offline tái lập bằng ba cấu hình trên cùng fixture 100 case:

```bash
python -m scripts.web_evidence_benchmark --config v1 --fixture tests/fixtures/web_evidence_v2/cases_100.json
python -m scripts.web_evidence_benchmark --config chars --fixture tests/fixtures/web_evidence_v2/cases_100.json
python -m scripts.web_evidence_benchmark --config v2 --fixture tests/fixtures/web_evidence_v2/cases_100.json
python -m scripts.web_evidence_benchmark --auto-fetch
```

Kết quả aggregate nằm trong [`docs/benchmarks/`](docs/benchmarks/README.md). Quyết định phát hành và phần chấm chất lượng model thật nằm ở [`RELEASE_DECISION.md`](docs/acceptance/web_evidence_v2_independent_validation_20261016/RELEASE_DECISION.md) và [`QUALITY_EVALUATION.md`](docs/acceptance/web_evidence_v2_independent_validation_20261016/QUALITY_EVALUATION.md); bộ câu live và cách chấm ở [`docs/WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md`](docs/WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md). Đây là **offline extractive proxy**, không phải chất lượng câu trả lời của model thật và không phải nghiệm thu production. V2 chưa đạt điều kiện pilot — lý do ở mục 2 của RELEASE_DECISION.

## Cài đặt nhanh

Yêu cầu: Docker + Docker Compose plugin, Telegram bot token, ít nhất một Chainnode API key cho primary route; Python 3.12 nếu chạy helper scripts trực tiếp ngoài container.

```bash
git clone https://github.com/vanphandinh/fucking-cool-ai-bot.git
cd fucking-cool-ai-bot
python scripts/sync_env.py
nano .env
docker compose up -d --build
docker compose logs -f bot
```

Cấu hình tối thiểu:

```env
BOT_TOKEN=...
AI_PROVIDERS__CHAINNODE__API_KEYS=...
AI_PROVIDERS__CHAINNODE__TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash
AI_PROVIDERS__CHAINNODE__VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
ADMIN_IDS=...
ALLOWED_GROUP_IDS=...
```

Không cần xKiro credential để Chainnode hoạt động. Khi `AI_PROVIDERS__XKIRO__API_KEYS` trống, xKiro có zero active targets.

## Production smoke

Sau deploy:

1. chạy `python scripts/sync_env.py` và yêu cầu exit `0`;
2. chạy lần hai, xác nhận không rewrite;
3. rebuild/restart bot;
4. smoke text request qua Chainnode primary;
5. smoke one-image request qua Chainnode vision primary;
6. smoke structured tools/continuation;
7. nếu xKiro đã qualified/configured, thực hiện controlled fallback rồi khôi phục Chainnode.

Operational rollback để tắt xKiro fallback:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

## Development verification

CI `Audit checks` chạy lint, dependency integrity, compileall, full unittest discovery và `pip-audit` trên Python 3.11/3.12; Python 3.12 còn validate Docker Compose và production image build.

Không commit `.env`, API keys, Telegram token hoặc các secret deployment khác.
