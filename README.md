# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.

**Default configured AI route: B.AI.** Runtime registry hỗ trợ B.AI và optional Chainnode cho text; Chainnode không hoạt động trừ khi deployment cấu hình credential/model và đưa `chainnode` vào `TEXT_PROVIDER_ORDER`. Vision hiện vẫn dùng B.AI.

**Architecture: generic capability-aware, health-aware, ordered multi-provider framework.** Core router, orchestrator, startup, metrics và Telegram handlers không coi B.AI là provider đặc biệt, nên có thể thêm provider khác mà không viết lại state machine chính.

Bot vẫn giữ web search, image search, direct URL reading, X/Twitter reader, Crawl4AI, vision input và Telegram-native HTML formatting.

## Tài liệu đang duy trì

- [Documentation index](docs/README.md) — canonical/current guides, historical records và source-of-truth order.
- [B.AI integration](docs/BAI_INTEGRATION.md) — model allowlist, adapter behavior, tool calling, health và synthesis.
- [Chainnode text provider](docs/CHAINNODE.md) — optional text route, qualified model, configuration và rollback.
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
          ├─ B.AI
          │    ├─ text slot
          │    └─ vision slot (max_images=1)
          └─ Chainnode
               └─ optional text slot
```

Provider contract là structural `AIProvider`; provider không bắt buộc kế thừa `OpenAICompatProvider`. `OpenAICompatProvider` chỉ là transport/parser reusable cho API kiểu OpenAI Chat Completions. B.AI và Chainnode hiện đều reuse implementation này qua provider-specific factories.

B.AI endpoint:

```text
POST https://api.b.ai/v1/chat/completions
```

Default production orders:

```env
TEXT_PROVIDER_ORDER=bai
VISION_PROVIDER_ORDER=bai
```

Framework hỗ trợ ordered fallback và bounded cyclic revisit cho đúng nhóm retryable transport failure. Với default order chỉ có `bai`, một B.AI outage kết thúc request sau bounded router recovery. Khi deployment cấu hình Chainnode và dùng order như `chainnode,bai` hoặc `bai,chainnode`, router có thể wrap lại provider trước đó khi provider đó vẫn eligible. Chainnode không tham gia vision routing.

---

## 2. B.AI model policy

Default production:

```env
BAI_TEXT_MODEL=qwen3.8-flash
BAI_VISION_MODEL=qwen3.8-flash
```

Allowlist B.AI hiện tại:

- `qwen3.8-flash`
- `mimo-v2.5`
- `hy3` — text-only trong integration này
- `glm-5.3-flash`

Không có automatic model rotation. Việc B.AI promotion/zero-credit có thể thay đổi upstream; repo không coi promotion là entitlement vĩnh viễn.

B.AI vision hiện advertise `max_images=1`. Đây là capability của adapter, không phải giới hạn kiến trúc của router.

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

Shared OpenAI-compatible adapter thực hiện đúng **một HTTP attempt cho mỗi `chat()` call** và gắn transport classification vào `ProviderError`. Cyclic policy chỉ mở rộng cho ba failure classes:

| Failure | Policy |
|---|---|
| `ConnectError` | retry cùng provider khi còn budget; sau đó rotate |
| `ConnectTimeout` | retry cùng provider khi còn budget; sau đó rotate |
| `ReadTimeout` | ưu tiên provider khác đang eligible; nếu không có thì retry cùng provider |
| `WriteTimeout` | không có cyclic revisit mới |
| `PoolTimeout` | không có cyclic revisit mới |
| HTTP `401/403/429/4xx/5xx` | giữ policy hiện tại; không thêm cyclic revisit |
| non-transient protocol/content error | fallback theo policy hiện tại; không cyclic revisit |

Request-scoped defaults:

```env
PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5
```

Ba tên trên là canonical env contract. Các tên verbose cũ `PROVIDER_RETRY_MAX_CONSECUTIVE_FAILURES`, `PROVIDER_RETRY_MAX_FAILURES_PER_PROVIDER`, `PROVIDER_RETRY_MAX_FAILURES_PER_REQUEST` vẫn được đọc như compatibility aliases; canonical thắng nếu cả hai dạng cùng tồn tại.

Một valid `provider.chat()` response, kể cả tool-call response, reset **chỉ consecutive counter của provider đó**. Provider cumulative và request cumulative transport-failure counters không reset trong cùng end-user request. Request mới bắt đầu với retry state mới.

Same-provider retry còn có token monotonic theo provider trong request. Token đã consume **không được hoàn lại bởi chat success**. Vì vậy chuỗi:

```text
ConnectTimeout
-> same-provider retry returns tool call
-> tool succeeds
-> ReadTimeout
```

không được cấp thêm same-provider retry chỉ vì tool-call chat trước đó thành công. Nếu ring chỉ có một provider và token đã consume, request dừng thay vì tạo call #4. Khi một failure yêu cầu rotation, scheduler exclude provider vừa fail cho đúng transition kế tiếp; một wrap về provider đó chỉ hợp lệ sau khi đã thật sự chọn provider khác.

Ví dụ với `TEXT_PROVIDER_ORDER=chainnode,bai`:

```text
chainnode ReadTimeout
-> bai ReadTimeout
-> chainnode success
```

Nếu cả hai tiếp tục fail với default consecutive limit:

```text
chainnode #1 fail
-> bai #1 fail
-> chainnode #2 fail/exhaust
-> bai #2 fail/exhaust
-> AllProvidersFailed
```

ConnectTimeout giữ same-provider-first semantics:

```text
chainnode #1 ConnectTimeout
-> chainnode #2 immediate same-provider retry
-> rotate nếu Chainnode bị exhaust/block
```

ReadTimeout alternative được **tính lại tại thời điểm failure trên toàn bộ cyclic ring**; router không dùng stale suffix snapshot.

Same-provider immediate retry diễn ra tại đúng AI HTTP continuation bị lỗi với cùng `messages` và active tool schema. Provider rotation/wrap không restart request từ base messages và không reset `_ToolBudget.calls`, `_ToolBudget.rounds`, `_ToolBudget.closed`, portable messages hoặc successful tool outputs.

Tool đã hoàn thành **không chạy lại** chỉ vì provider routing thay đổi. Tool budget là request-wide:

- `MAX_TOOL_ROUNDS` — default `2`;
- hard cap nội bộ: tối đa `8` tool calls/request;
- tối đa `2` tool calls chạy song song.

Router tách hai state:

- provider-local continuation state — có thể chứa metadata riêng của provider;
- portable state — chỉ chứa canonical messages, generic tool calls/results và normalized evidence.

Same-provider retry giữ exact provider-local continuation. Khi rotate/wrap, portable state mang tool evidence đã hoàn thành sang provider tiếp theo nhưng không mang vendor-specific assistant metadata.

Request retry state tách khỏi shared `ProviderHealth`. Retryable transport error được giữ pending cho logical streak: nếu provider recover thì pending error bị clear và shared health không tăng; nếu provider bị exhaust/block hoặc request hoàn tất bằng provider khác trước khi nó recover thì pending streak được flush **một logical health failure** đúng một lần.

Shared health có causal generation. `record_success()` và immediate `record_error()` advance generation; pending transport failure capture generation khi streak bắt đầu và deferred failure chỉ apply nếu generation chưa đổi. Deferred apply không advance generation, nên hai independent unresolved concurrent failures vẫn có thể count hai lần. Nhờ đó stale pending ReadTimeout không thể overwrite một success mới hơn hoặc rút cooldown `429 Retry-After` mới hơn về transient cooldown cũ.

Fallback statistics đếm provider transitions, không đếm attempts. `chainnode -> chainnode` immediate retry không tạo fallback; `chainnode -> bai -> chainnode` tạo hai transitions.

Khi tool budget đóng/hết, router tạo **Fresh Synthesis** từ original request + bounded untrusted plain-text evidence, tắt tools và bỏ structured provider-local trajectory. Nếu provider hiện tại fail ở synthesis stage và còn provider eligible, provider sau nhận cùng generic Fresh Synthesis context với tools vẫn tắt. Với B.AI, no-tool request còn đặt `tool_choice=none`.

Tool call phát sinh sau khi tools đã tắt không bao giờ được execute.

`QUESTION_CONTROLS_ENABLED=0` là default rollback-safe và giữ `QUESTION_TIMEOUT_SEC` làm outer hard wall-clock guard của legacy handler. Khi `QUESTION_CONTROLS_ENABLED=1`, toàn câu hỏi **không** bị bọc bởi hard deadline này: hết interval chỉ chuyển sang Tiếp tục/Dừng, trong khi từng provider/search/URL/media operation vẫn có timeout và capacity bound riêng.

---

## 4. Cài đặt nhanh

Yêu cầu:

- Docker + Docker Compose plugin;
- Telegram bot token;
- credential của ít nhất một text provider trong configured order; default là B.AI API key;
- Python 3.12 nếu chạy helper scripts trực tiếp ngoài container.

```bash
git clone https://github.com/vanphandinh/fucking-cool-ai-bot.git
cd fucking-cool-ai-bot
python scripts/sync_env.py
nano .env
```

`sync_env.py` tự tạo `.env` từ `.env.example` nếu chưa có và dùng mode `0600` cho file mới.

Cấu hình tối thiểu default:

```env
BOT_TOKEN=...
BAI_API_KEY=...
TEXT_PROVIDER_ORDER=bai
VISION_PROVIDER_ORDER=bai
ADMIN_IDS=...
ALLOWED_GROUP_IDS=...
```

Khởi động:

```bash
docker compose up -d --build
docker compose logs -f bot
```

---

## 5. Cấu hình AI + vision

```env
BAI_API_KEY=
BAI_TEXT_MODEL=qwen3.8-flash
BAI_REQUEST_TIMEOUT_SEC=60.0

CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODEL=
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=bai
PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5

VISION_ENABLED=1
BAI_VISION_MODEL=qwen3.8-flash
VISION_PROVIDER_ORDER=bai
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

`BAI_REQUEST_TIMEOUT_SEC` và `CHAINNODE_REQUEST_TIMEOUT_SEC` điều khiển **per-HTTP-attempt read timeout** của provider tương ứng. Shared OpenAI-compatible transport dùng `connect=8s`, `write=20s`, `pool=5s`. `QUESTION_TIMEOUT_SEC` chỉ là outer hard deadline của legacy path khi `QUESTION_CONTROLS_ENABLED=0`; không tăng key legacy này để bù retry policy hoặc để điều khiển renewable mode. Xem [docs/QUESTION_CONTROLS.md](docs/QUESTION_CONTROLS.md) cho time semantics, quyền callback và rollout/rollback.

Retry bounds validation:

- consecutive: `1..5`;
- per-provider cumulative: `1..10` và phải `>= consecutive`;
- per-request cumulative: `1..20` và phải `>= consecutive`.

Legacy verbose retry env names vẫn được chấp nhận khi runtime đọc Settings. Khi chạy `scripts/sync_env.py`, legacy value được migrate sang canonical name; canonical value thắng nếu hai dạng cùng có. Lần sync tiếp theo là idempotent nếu không có thay đổi khác.

`VISION_ENABLED=0` chỉ tắt image understanding; text vẫn hoạt động.

Effective image limit là giới hạn nhỏ hơn giữa application limit và capability của các configured vision providers. Với production hiện tại, B.AI advertise `max_images=1`, nên effective limit là một ảnh.

---

## 6. Thêm AI provider mới

Một provider thông thường nên được add mà **không sửa** router state machine, orchestrator, stats, startup hoặc Telegram handlers:

1. Implement `AIProvider` trực tiếp, hoặc reuse `OpenAICompatProvider` nếu upstream dùng OpenAI-compatible Chat Completions.
2. Thêm provider-specific settings/credentials với namespace riêng.
3. Tạo provider-family factory trả về các configured route slots.
4. Register factory trong `PROVIDER_FACTORIES`.
5. Khai báo `ProviderCapabilities` cho từng slot và thêm provider key vào `TEXT_PROVIDER_ORDER` / `VISION_PROVIDER_ORDER` phù hợp.
6. Pass provider registry, ordered routing/fallback, portability và provider-specific regression tests.

Provider-specific payload mapping, response parsing, auth/header, reasoning metadata và compatibility quirks phải nằm trong adapter, không đưa vào core router. Transport retry policy là generic router concern; adapter chỉ classify transport failures.

Provider slot identity là `(provider.name, route)`. Router reject duplicate identity để retry/health state keyed theo provider name không bị nhập nhằng. Cùng provider name có thể có một text slot và một vision slot vì route khác nhau; B.AI hiện dùng đúng pattern này.

---

## 7. Nâng cấp deployment cũ

Không copy đè `.env` bằng `.env.example`. Sau `git pull`, backup rồi sync:

```bash
cp .env .env.bak
python scripts/sync_env.py
```

Script giữ value của key còn tồn tại, xóa key không còn trong `.env.example`, thêm key mới theo template, và bảo đảm `.env` không có execute/group/other permission bits. File mới dùng mode `0600`; permission-only repair không rewrite nội dung. `.env.bak` và `.env.*.bak` được ignore khỏi Git/build context nhưng vẫn chứa secret production nên không được upload/chia sẻ.

Migration hiện tại:

- giữ B.AI settings hiện có;
- thêm/giữ các `CHAINNODE_*` keys cho optional Chainnode text provider;
- migrate ba retry keys legacy sang canonical `PROVIDER_RETRY_MAX_CONSECUTIVE`, `PROVIDER_RETRY_MAX_PER_PROVIDER`, `PROVIDER_RETRY_MAX_PER_REQUEST`; canonical thắng nếu cả hai dạng tồn tại;
- thêm canonical retry defaults nếu deployment chưa có cả canonical lẫn legacy value;
- giữ `TEXT_PROVIDER_ORDER` / `VISION_PROVIDER_ORDER` vì chúng vẫn là generic routing config;
- thêm các question-control keys mới từ `.env.example` mà không đổi value của `QUESTION_TIMEOUT_SEC`;
- xóa credential/model variables của Gemini/Groq/OpenRouter/Cloudflare Workers AI vì chúng không còn trong template;
- giữ Telegram/search/Crawl4AI values nếu key còn tồn tại.

`sync_env.py` cố ý giữ value hiện có. Vì vậy deployment đang có `BAI_REQUEST_TIMEOUT_SEC=30.0` hoặc `CHAINNODE_REQUEST_TIMEOUT_SEC=30.0` **không tự đổi thành 60.0**. Khi rollout retry policy, giữ nguyên timeout production hiện tại; đánh giá timeout riêng sau khi đã quan sát retry/fallback behavior.

Nếu order cũ vẫn chứa provider đã bị xóa, ví dụ `bai,gemini`, `sync_env.py` **giữ nguyên intent đó** thay vì silently sửa. Startup sẽ reject unknown provider rõ ràng. Text provider keys hiện được register là `bai` và `chainnode`; vision provider hiện chỉ có `bai`. Default orders vẫn là:

```env
TEXT_PROVIDER_ORDER=bai
VISION_PROVIDER_ORDER=bai
```

Chi tiết: [docs/ENV_SYNC.md](docs/ENV_SYNC.md).

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

`auto` ưu tiên SearXNG rồi dùng DDGS theo policy resilience/cache hiện có. Trong renewable mode, shared cache vẫn dùng chung nhưng một live singleflight execution không được share giữa hai jobs có consent owner khác nhau; backend attempts đi qua operation gate và pause/queue không bị tính vào total operation budget.

Crawl4AI là optional URL rendering backend:

```env
CRAWL4AI_ENABLED=1
CRAWL4AI_URL=http://crawl4ai:11235
CRAWL4AI_API_TOKEN=
CRAWL4AI_TIMEOUT_SEC=25.0
CRAWL4AI_MAX_CHARS=12000
```

Direct URL pipeline hiện là X-specific resolution trước, sau đó Crawl4AI nếu active, rồi generic reader. Nếu Crawl4AI không được cấu hình đầy đủ, URL reader degrade về generic reader thay vì làm bot fail startup. DDGS và DNS resolver dùng bounded synchronous-thread capacity để asyncio cancellation không giả vờ rằng thread nền đã dừng.

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

Plain image không có text/caption trigger sẽ không tự gọi bot. Forum topic luôn cô lập history bằng `(chat_id, message_thread_id)`. Legacy mode vẫn giữ per-conversation lock; renewable mode dùng immutable history snapshot theo job và không giữ chat lock xuyên toàn request, nên một job đang chờ consent không khóa job khác trong cùng group/topic.

Trong renewable mode, owner có thể bấm **Tiếp tục** hoặc **Dừng**; admin chỉ có quyền Dừng. Continue phải khớp generation hiện tại, còn Stop của cùng job vẫn hữu dụng khi status vừa đổi. Registry Release 1 nằm trong RAM nên restart không resume job và callback cũ không tạo lại request.

Provider health state là per-slot:

- `401/403` → disable slot đến process restart;
- `429` → cooldown, ưu tiên numeric `Retry-After`;
- unresolved network transport streak → một logical transient health failure sau request-scoped recovery;
- success → reset transient state;
- deferred transport health chỉ apply nếu causal generation chưa bị success/immediate error mới hơn thay đổi.

Retry/rotation logs chỉ chứa provider name + transport kind/decision, ví dụ:

```text
AI provider chainnode transient connect_timeout; retry same provider
AI provider chainnode lỗi: transport failure: read_timeout
AI provider bai lỗi: transport failure: read_timeout
```

Không log API key, Authorization, full prompt, image base64 hay raw sensitive tool output.

`/status` hiển thị generic text/vision provider list, configured order, provider distribution, last provider, fallback transition count và cooldown/unavailable state. Fallback count là transition count; same-provider retry không inflate metric.

---

## 10. Verification

```bash
python -m unittest tests.test_provider_retry_state -v
python -m unittest tests.test_provider_transport_retry -v
python -m unittest tests.test_provider_local_recovery_retry -v
python -m unittest tests.test_provider_retry_health -v
python -m unittest tests.test_config_regressions -v
python -m unittest tests.test_ai_transport_resilience -v
python -m unittest tests.test_provider_routing -v
python -m unittest tests.test_provider_portability -v
python -m unittest tests.test_provider_observability -v
python -m unittest tests.test_job_control -v
python -m unittest tests.test_job_operations -v
python -m unittest tests.test_job_router -v
python -m unittest tests.test_job_manager -v
python -m unittest tests.test_job_telegram -v
python -m unittest tests.test_job_lifecycle -v
python -m unittest tests.test_question_renewal_flow -v
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m pip check
python -m compileall -q app tests scripts
cp .env.example .env
python scripts/sync_env.py
python scripts/sync_env.py
docker compose config --quiet
docker build --tag fcai-renewable-audit .
```

GitHub Actions chạy Python 3.11/3.12; Python 3.12 còn validate Compose/SearXNG YAML và build production image.

Manual smoke **sau khi operator quyết định deploy** nên gồm các flow cũ cộng với renewable controls trên bot/group thử nghiệm được chỉ định:

1. normal text query không tool;
2. query cần web search/tool;
3. direct URL;
4. một ảnh;
5. request vượt effective image limit;
6. `/status`;
7. hai lần renewal liên tiếp giữ cùng evidence/retry/tool state;
8. Stop khi queued/running/consent;
9. paused job A không chặn job B;
10. restart làm nút job cũ stale, không tự resume;
11. natural `ConnectTimeout -> same-provider retry -> success/fallback`;
12. natural `ReadTimeout -> eligible alternative`;
13. all providers bounded -> `AllProvidersFailed` không infinite loop;
14. tool evidence tồn tại sau rotation/wrap và completed tool không chạy lại.

Rollout renewable controls phải deploy code với `QUESTION_CONTROLS_ENABLED=0` trước, smoke trên môi trường được operator chỉ định, rồi mới bật flag nếu đạt. Rollback đặt flag về `0` và recreate/restart deployment. Repo/PR không tự deploy hoặc gửi test vào group thật.

Các phase circuit breaker CLOSED/OPEN/HALF_OPEN, per-provider bulkhead, adaptive scoring và hedged requests là follow-up riêng, không phải behavior hiện tại.
