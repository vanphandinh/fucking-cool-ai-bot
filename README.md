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

`README.md`, `.env.example`, runtime source, Docker/CI và các active operator guide là source of truth cho current deployment contract.

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
