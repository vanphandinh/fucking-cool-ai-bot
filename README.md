# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.

**Default configured AI route: Chainnode primary -> xKiro fallback.** Text và vision đều dùng cùng ordered, capability-aware, health-aware multi-provider framework. Chainnode giữ vai trò primary; xKiro chỉ tham gia khi deployment có `XKIRO_API_KEY` và model ID đã được live-qualify.

Bot vẫn giữ web search, image search, direct URL reading, X/Twitter reader, Crawl4AI, vision input, renewable question controls và Telegram-native HTML formatting.

## Tài liệu đang duy trì

- [Documentation index](docs/README.md) — canonical/current guides, historical records và source-of-truth order.
- [Chainnode primary provider](docs/CHAINNODE.md) — qualified text/vision split, configuration, probes và rollback.
- [xKiro fallback provider](docs/XKIRO.md) — adapter, live `/v1/models` qualification, rollout và rollback.
- [Telegram vision input](docs/telegram-vision-input.md) — input ảnh, capability routing và memory safety.
- [Telegram-native formatting](docs/TELEGRAM_FORMATTING.md).
- [Renewable question controls](docs/QUESTION_CONTROLS.md) — progress, Tiếp tục/Dừng, local bounds, rollout và rollback.
- [Search resilience](docs/SEARCH_RESILIENCE.md).
- [Crawl4AI URL reading](docs/CRAWL4AI_INTEGRATION.md).
- [X/Twitter content fetching](docs/X_CONTENT_FETCHING.md).
- [Đồng bộ `.env`](docs/ENV_SYNC.md).
- [SearXNG production trên VPS](DEPLOY_SEARXNG_VPS.md).

> `README.md`, `.env.example`, source code, Docker/CI và các guide runtime ở trên là source of truth. `docs/superpowers/` là snapshot thiết kế/kế hoạch lịch sử nên có thể chứa tên provider đã bị loại khỏi runtime.

---

## 1. Kiến trúc AI hiện tại

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
   ├─ health/cooldown filtering
   └─ bounded cyclic provider scheduler
          │
          ├─ Chainnode
          │    ├─ text primary
          │    └─ vision primary (max_images=1)
          └─ xKiro
               ├─ text fallback
               └─ vision fallback (max_images=1)
```

Provider contract là structural `AIProvider`; provider không bắt buộc kế thừa `OpenAICompatProvider`. Chainnode và xKiro đều reuse shared OpenAI-compatible transport/parser qua provider-specific factories.

Production defaults:

```env
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Chainnode đã được qualify riêng cho text và vision. xKiro model IDs không hard-code trong runtime; `.env.example` cố ý để `XKIRO_TEXT_MODEL` và `XKIRO_VISION_MODEL` trống cho tới khi current candidates vượt live qualification.

xKiro runtime endpoint:

```text
POST https://api.xkiro.com/v1/chat/completions
```

Cả hai provider gửi non-stream request với `stream=false`. Runtime không query model catalog khi startup hoặc mỗi request.

---

## 2. xKiro fallback model policy

`GET https://api.xkiro.com/v1/models` là source of truth cho model availability, free tier, pricing và capabilities. Không chọn model bằng snapshot cũ, suffix tên model hoặc allowlist hard-code.

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

Vision candidate phải thêm:

```text
capabilities.vision == true
known-image understanding
vision + structured tool flow
```

Probe retained:

```bash
export XKIRO_API_KEY='...'
python scripts/probe_xkiro.py \
  --text-model '<candidate-text-id>' \
  --vision-model '<candidate-vision-id>' \
  --image './known-test-image.png'
```

Probe emit JSONL, đọc key chỉ từ environment, không log key/Authorization header, honor bounded `429 Retry-After`, và fail closed trên malformed response.

Chỉ sau khi probe live pass mới cấu hình:

```env
XKIRO_TEXT_MODEL=<qualified-live-model-id>
XKIRO_VISION_MODEL=<qualified-live-model-id>
```

---

## 3. Bounded cyclic transport retry, tool state và Fresh Synthesis

Router chọn candidate theo route order, capability, shared health và request-scoped retry state:

```text
configured capability-filtered ring
 -> currently healthy + request-eligible slot?
 -> provider attempt
 -> bounded same-provider retry nếu policy yêu cầu?
 -> rotate forward; wrap về đầu ring khi cần
 -> stop khi không còn eligible provider hoặc cumulative request budget hết
```

Shared OpenAI-compatible adapter thực hiện đúng **một HTTP attempt cho mỗi `chat()` call** và gắn transport classification vào `ProviderError`.

| Failure | Policy |
|---|---|
| `ConnectError` | retry cùng provider khi còn budget; sau đó rotate |
| `ConnectTimeout` | retry cùng provider khi còn budget; sau đó rotate |
| `ReadTimeout` | ưu tiên provider khác đang eligible; nếu không có thì retry cùng provider |
| `WriteTimeout` | không có cyclic revisit mới |
| `PoolTimeout` | không có cyclic revisit mới |
| HTTP `401/403/429/4xx/5xx` | giữ existing health/fallback policy; không thêm cyclic revisit |
| non-transient protocol/content error | fallback theo existing policy; không cyclic revisit |

Request-scoped defaults:

```env
PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5
```

Ba tên trên là canonical env contract. Các verbose legacy aliases vẫn được đọc để nâng cấp deployment cũ; canonical value thắng nếu cả hai dạng cùng tồn tại.

Một valid `provider.chat()` response reset **chỉ consecutive counter** của provider đó. Provider cumulative và request cumulative transport-failure counters không reset trong cùng end-user request. Request mới bắt đầu với retry state mới.

Same-provider retry có token monotonic theo provider trong request. Token đã consume không được hoàn lại bởi chat success. Ví dụ:

```text
ConnectTimeout
-> same-provider retry returns tool call
-> tool succeeds
-> ReadTimeout
```

không được cấp thêm same-provider retry chỉ vì tool-call chat trước đó thành công.

Ví dụ ordered fallback:

```text
chainnode ReadTimeout
-> xkiro ReadTimeout
-> chainnode success
```

Nếu cả hai tiếp tục fail với default consecutive limit:

```text
chainnode #1 fail
-> xkiro #1 fail
-> chainnode #2 fail/exhaust
-> xkiro #2 fail/exhaust
-> AllProvidersFailed
```

ReadTimeout alternative được tính lại tại thời điểm failure trên toàn cyclic ring; router không dùng stale suffix snapshot.

Same-provider immediate retry diễn ra tại đúng AI continuation bị lỗi với cùng `messages` và active tool schema. Provider rotation/wrap không restart request từ base messages và không reset tool budget, portable messages hoặc successful tool outputs.

Tool đã hoàn thành **không chạy lại** chỉ vì provider routing thay đổi. Tool budget là request-wide:

- `MAX_TOOL_ROUNDS` — default `2`;
- hard cap nội bộ: tối đa `8` tool calls/request;
- tối đa `2` tool calls chạy song song.

Router tách provider-local continuation state khỏi portable state. Khi rotate/wrap, portable state mang canonical evidence/tool results sang provider tiếp theo nhưng không mang provider-local reasoning metadata.

Fallback statistics đếm provider transitions, không đếm attempts. `chainnode -> chainnode` immediate retry không tạo fallback; `chainnode -> xkiro -> chainnode` tạo hai transitions.

Khi tool budget đóng/hết, router tạo **Fresh Synthesis** từ original request + bounded untrusted plain-text evidence. Fresh Synthesis:

- không gửi `tools`;
- không replay structured provider-local tool history;
- không inject provider-specific `tool_choice=none` workaround;
- không execute tool call phát sinh sau khi budget đã đóng;
- giữ cùng provider-neutral evidence nếu phải fallback sang provider khác.

`QUESTION_CONTROLS_ENABLED=0` là default rollback-safe và giữ `QUESTION_TIMEOUT_SEC` làm outer hard wall-clock guard của legacy handler. Khi `QUESTION_CONTROLS_ENABLED=1`, hết interval chuyển sang Tiếp tục/Dừng trong khi từng provider/search/URL/media operation vẫn có timeout và capacity bound riêng.

---

## 4. Cài đặt nhanh

Yêu cầu:

- Docker + Docker Compose plugin;
- Telegram bot token;
- Chainnode API key cho production primary;
- Python 3.12 nếu chạy helper scripts trực tiếp ngoài container.

```bash
git clone https://github.com/vanphandinh/fucking-cool-ai-bot.git
cd fucking-cool-ai-bot
python scripts/sync_env.py
nano .env
```

`sync_env.py` tự tạo `.env` từ `.env.example` nếu chưa có và dùng mode `0600` cho file mới.

Cấu hình tối thiểu để chạy Chainnode primary:

```env
BOT_TOKEN=...
CHAINNODE_API_KEY=...
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
ADMIN_IDS=...
ALLOWED_GROUP_IDS=...
```

Không cần xKiro credential để Chainnode hoạt động. Khi `XKIRO_API_KEY` trống, xKiro factory trả zero active slots.

Khởi động:

```bash
docker compose up -d --build
docker compose logs -f bot
```

---

## 5. Cấu hình AI + vision

```env
CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0

XKIRO_API_KEY=
XKIRO_TEXT_MODEL=
XKIRO_VISION_MODEL=
XKIRO_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5

VISION_ENABLED=1
VISION_PROVIDER_ORDER=chainnode,xkiro
MAX_IMAGES_PER_REQUEST=1
MAX_IMAGE_BYTES=8388608
MAX_TOTAL_IMAGE_BYTES=12582912

QUESTION_CONTROLS_ENABLED=0
QUESTION_RENEWAL_INTERVAL_SEC=180
QUESTION_PROGRESS_INTERVAL_SEC=25
QUESTION_MAX_INFLIGHT_OPERATIONS=2
QUESTION_MAX_PENDING_JOBS=20
QUESTION_MAX_JOBS_PER_USER=2
AI_ATTEMPT_TOTAL_TIMEOUT_SEC=90
URL_READ_TOTAL_TIMEOUT_SEC=40
TOOL_CALL_TOTAL_TIMEOUT_SEC=45
```

`CHAINNODE_REQUEST_TIMEOUT_SEC` và `XKIRO_REQUEST_TIMEOUT_SEC` điều khiển **per-HTTP-attempt read timeout** của provider tương ứng. Shared OpenAI-compatible transport dùng `connect=8s`, `write=20s`, `pool=5s`.

xKiro validation:

- key + xKiro selected for text => `XKIRO_TEXT_MODEL` required;
- vision enabled + key + xKiro selected for vision => `XKIRO_VISION_MODEL` required;
- no key => zero xKiro slots.

Retry bounds validation:

- consecutive: `1..5`;
- per-provider cumulative: `1..10` và phải `>= consecutive`;
- per-request cumulative: `1..20` và phải `>= consecutive`.

`VISION_ENABLED=0` chỉ tắt image understanding; text vẫn hoạt động. Effective image limit là giới hạn nhỏ hơn giữa application limit và capabilities của configured vision providers. Cả Chainnode và xKiro vision slots hiện advertise `max_images=1`.

---

## 6. Thêm AI provider mới

Một provider thông thường nên được add mà **không sửa** router state machine, orchestrator, stats, startup hoặc Telegram handlers:

1. Implement `AIProvider` trực tiếp, hoặc reuse `OpenAICompatProvider` nếu upstream dùng OpenAI-compatible Chat Completions.
2. Thêm provider-specific settings/credentials với namespace riêng.
3. Tạo provider-family factory trả về configured route slots.
4. Register factory trong `PROVIDER_FACTORIES`.
5. Khai báo `ProviderCapabilities` cho từng slot và thêm provider key vào route order phù hợp.
6. Pass registry, ordered routing/fallback, portability và provider-specific regression tests.

Provider-specific payload mapping, response parsing, auth/header, reasoning metadata và compatibility quirks nằm trong adapter, không đưa vào core router. Transport retry policy là generic router concern; adapter chỉ classify transport failures.

Provider slot identity là `(provider.name, route)`. Cùng provider name có thể có một text slot và một vision slot vì route khác nhau; Chainnode và xKiro đều dùng pattern này.

---

## 7. Nâng cấp deployment cũ

Không copy đè `.env` bằng `.env.example`. Sau `git pull`, backup rồi sync:

```bash
cp .env .env.bak
python scripts/sync_env.py
```

Script:

- giữ value của key còn tồn tại;
- thêm new keys từ template;
- xoá keys của provider đã retire vì chúng không còn trong template;
- giữ Chainnode credentials/models;
- giữ `XKIRO_*` values nếu operator đã cấu hình;
- không copy credential giữa hai services khác nhau;
- nếu provider order chứa legacy retired token, canonicalize thành `chainnode,xkiro` rồi giữ custom names phía sau;
- nếu order không chứa legacy token, giữ nguyên operator value;
- migrate provider-retry verbose aliases sang canonical keys;
- bảo đảm `.env` không có group/other/execute permission bits;
- idempotent khi chạy lần hai mà input không đổi.

Chi tiết: [docs/ENV_SYNC.md](docs/ENV_SYNC.md).

Sau migration, normal production order:

```env
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Operational rollback là Chainnode-only:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

---

## 8. Search / URL reading

Search không phụ thuộc provider registry.

```env
SEARCH_BACKEND=auto
SEARXNG_URL=http://searxng:8080
SEARXNG_TIMEOUT_SEC=7.0
DDGS_TIMEOUT_SEC=8.0
SEARCH_TOTAL_TIMEOUT_SEC=15.0
```

`auto` ưu tiên SearXNG rồi dùng DDGS theo bounded resilience/cache policy hiện có. Trong renewable mode, shared cache vẫn dùng chung nhưng live singleflight execution không được share giữa hai jobs có consent owner khác nhau.

Crawl4AI là optional URL rendering backend:

```env
CRAWL4AI_ENABLED=1
CRAWL4AI_URL=http://crawl4ai:11235
CRAWL4AI_API_TOKEN=
CRAWL4AI_TIMEOUT_SEC=25.0
CRAWL4AI_MAX_CHARS=12000
```

Direct URL pipeline: X-specific resolution trước, Crawl4AI nếu active, rồi generic reader. Nếu Crawl4AI không được cấu hình đầy đủ, URL reader degrade về generic reader thay vì fail startup.

---

## 9. Telegram usage và observability

| Flow | Ví dụ / hành vi |
|---|---|
| `/ask` | `/ask giải thích blockchain ngắn gọn` |
| Mention | `@FuckingCoolAIbot giá vàng hôm nay?` |
| Tìm ảnh | `@FuckingCoolAIbot tìm cho tôi ảnh capybara` |
| Đọc URL | gửi URL + yêu cầu đọc/tóm tắt |
| X/Twitter | gửi status URL + câu hỏi |
| Ảnh | JPEG/PNG/WebP + caption trigger, trong effective capability limit |
| Reply ảnh | reply ảnh rồi tag bot hoặc `/ask` |
| `/help` | hướng dẫn ngắn |
| `/status` | admin-only status |

Plain image không có text/caption trigger sẽ không tự gọi bot. Forum topic luôn cô lập history bằng `(chat_id, message_thread_id)`.

Provider health state là per-slot:

- `401/403` → disable slot đến process restart;
- `429` → cooldown, ưu tiên numeric `Retry-After`;
- unresolved network transport streak → một logical transient health failure sau request-scoped recovery;
- success → reset transient state.

Retry/rotation logs chỉ chứa provider name + transport kind/decision, ví dụ:

```text
AI provider chainnode transient connect_timeout; retry same provider
AI provider chainnode lỗi: transport failure: read_timeout
AI provider xkiro lỗi: transport failure: read_timeout
```

Không log API key, Authorization, full prompt, image base64 hay raw sensitive tool output.

`/status` hiển thị generic text/vision provider list, configured order, provider distribution, last provider, fallback transition count và cooldown/unavailable state. Same-provider retry không inflate fallback metric.

---

## 10. Verification

```bash
python -m unittest tests.test_xkiro_provider -v
python -m unittest tests.test_xkiro_probe -v
python -m unittest tests.test_chainnode_provider -v
python -m unittest tests.test_provider_registry -v
python -m unittest tests.test_provider_env_migration tests.test_sync_env -v
python -m unittest tests.test_provider_transport_retry -v
python -m unittest tests.test_provider_retry_state -v
python -m unittest tests.test_provider_retry_health -v
python -m unittest tests.test_provider_portability -v
python -m unittest tests.test_tool_fallback_recovery -v
python -m unittest tests.test_fresh_synthesis_routing -v
python -m unittest tests.test_vision -v
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m pip check
python -m compileall -q app tests scripts
cp .env.example .env
python scripts/sync_env.py
python scripts/sync_env.py
docker compose config --quiet
docker build --tag fcai-audit .
```

GitHub Actions chạy Python 3.11/3.12; Python 3.12 còn validate Compose/SearXNG YAML và build production image.

Manual smoke sau deployment:

1. normal text query -> Chainnode first;
2. web-search/tool flow;
3. direct URL;
4. one-image request -> Chainnode vision first;
5. request vượt effective image limit;
6. `/status`;
7. controlled Chainnode failure -> xKiro fallback sau khi xKiro đã live-qualified;
8. verify completed tool không chạy lại khi rotate provider;
9. restore valid Chainnode credentials immediately;
10. verify normal traffic quay lại primary.

Repo/PR không tự deploy hoặc gửi test vào group thật.
