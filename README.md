# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.

**Current registered production providers: B.AI + optional Aurora text fallback.** B.AI cung cấp text + vision; Aurora chỉ cung cấp text và tự skip khi chưa có `AURORA_API_KEY`. Gemini, Groq, OpenRouter và Cloudflare Workers AI vẫn đã bị xóa khỏi source/runtime và không được PR này khôi phục.

**Architecture: generic capability-aware, health-aware, ordered multi-provider framework.** Core router, orchestrator, startup, metrics và Telegram handlers không coi B.AI/Aurora là provider đặc biệt. Provider family được add qua registry, không viết provider-name branch vào router state machine.

Bot vẫn giữ web search, image search, direct URL reading, X/Twitter reader, Crawl4AI, vision input và Telegram-native HTML formatting.

## Tài liệu đang duy trì

- [B.AI integration](docs/BAI_INTEGRATION.md) — model allowlist, adapter behavior, tool calling, health và synthesis.
- [Aurora integration](docs/AURORA_INTEGRATION.md) — registry wiring, private sidecar, credential boundary, probe, rollout và rollback.
- [Telegram vision input](docs/telegram-vision-input.md) — input ảnh, capability routing và memory safety.
- [Telegram-native formatting](docs/TELEGRAM_FORMATTING.md).
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
   └─ ordered provider attempts
          │
          ▼
      PROVIDER_FACTORIES
          ├─ bai
          │    ├─ text slot
          │    └─ vision slot (max_images=1)
          └─ aurora
               └─ text slot only
```

Provider contract là structural `AIProvider`; provider không bắt buộc kế thừa `OpenAICompatProvider`. `OpenAICompatProvider` chỉ là transport/parser reusable cho API kiểu OpenAI Chat Completions. B.AI và Aurora hiện đều reuse transport này, nhưng được tạo qua provider-family factory riêng.

Current production orders:

```env
TEXT_PROVIDER_ORDER=bai,aurora
VISION_PROVIDER_ORDER=bai
```

Nếu chỉ có `BAI_API_KEY`, effective configured text providers vẫn chỉ là `bai`; Aurora family nằm trong order nhưng không tạo slot cho tới khi có `AURORA_API_KEY`. Khi cả hai được cấu hình, text fallback là:

```text
B.AI -> Aurora
```

Vision vẫn B.AI-only. `router.py` không import B.AI hay Aurora; nó chỉ dùng registry + generic provider protocol.

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

## 3. Aurora fallback

Aurora là **optional text-only provider family**. Default settings:

```env
AURORA_BASE_URL=http://aurora:8080/v1
AURORA_API_KEY=
AURORA_MODEL=auto
AURORA_REQUEST_TIMEOUT_SEC=90.0
AURORA_IMAGE=ghcr.io/aurora-develop/aurora:v2.6.3
AURORA_CREDENTIAL_FILE=./aurora/session_tokens.txt
AURORA_CREDENTIAL_TARGET=/home/nonroot/session_tokens.txt
```

`AURORA_API_KEY` chỉ là internal bot-to-Aurora service key. ChatGPT Web session/access/refresh credentials không đi vào Python settings hay `.env`; chúng được mount read-only trực tiếp vào sidecar Aurora và bị Git-ignore.

Private profile:

```bash
mkdir -p aurora
chmod 700 aurora
# create aurora/session_tokens.txt locally, one token per line
chmod 600 aurora/session_tokens.txt

docker compose --profile aurora up -d aurora
```

Aurora không publish port ra host. Compose pin `v2.6.3`, tắt external token/history/free-account behavior và bật tool calling theo contract đã test.

Chi tiết: [docs/AURORA_INTEGRATION.md](docs/AURORA_INTEGRATION.md).

---

## 4. Ordered fallback, tool state và Fresh Synthesis

Router chọn candidate theo route order, capability và health:

```text
configured order
 -> configured slot?
 -> capable slot?
 -> healthy?
 -> attempt provider
 -> provider-local failure?
 -> next capable healthy provider
```

Chỉ `ProviderError` là provider-local failure hợp lệ để outer router fallback. Lỗi lập trình/runtime bất ngờ thoát khỏi provider contract không bị che bằng việc thử provider tiếp theo.

Tool budget là **request-wide**, không reset khi đổi provider:

- `MAX_TOOL_ROUNDS` — default `2`;
- hard cap nội bộ: tối đa `8` tool calls/request;
- tối đa `2` tool calls chạy song song.

Router tách hai state:

- provider-local continuation state — có thể chứa metadata riêng của provider;
- portable state — chỉ chứa canonical messages, generic tool calls/results và normalized evidence.

Khi tool budget đóng/hết, router tạo **Fresh Synthesis** từ original request + bounded untrusted plain-text evidence, tắt tools và bỏ structured provider-local trajectory. Nếu provider hiện tại fail ở synthesis stage và còn provider sau, provider sau nhận cùng generic Fresh Synthesis context với tools vẫn tắt.

Aurora sử dụng chính generic tool loop hiện tại:

```text
Aurora tool_calls -> bot executes approved tool -> role=tool -> Aurora final text
```

Không có Aurora-specific tool executor/router loop.

---

## 5. Provider health

Default provider health semantics vẫn giữ từ PR #38:

- `401/403` → disable slot đến process restart;
- `429` → cooldown, ưu tiên numeric `Retry-After`;
- network/`5xx` transient → cooldown theo generic health policy;
- success → reset transient state.

Aurora opt-in policy riêng qua generic constructor parameter: `401/403` dùng temporary cooldown 60 giây thay vì permanent disable; numeric `Retry-After` nếu có sẽ override 60 giây. Đây là policy của slot, không phải provider-name special case trong router.

---

## 6. Cài đặt nhanh

Yêu cầu:

- Docker + Docker Compose plugin;
- Telegram bot token;
- ít nhất một configured text provider; production cơ bản vẫn có thể chạy chỉ với B.AI;
- Python 3.12 nếu chạy helper scripts trực tiếp ngoài container.

```bash
git clone https://github.com/vanphandinh/fucking-cool-ai-bot.git
cd fucking-cool-ai-bot
cp .env.example .env
nano .env
```

Cấu hình tối thiểu B.AI-only:

```env
BOT_TOKEN=...
BAI_API_KEY=...
TEXT_PROVIDER_ORDER=bai,aurora
VISION_PROVIDER_ORDER=bai
ADMIN_IDS=...
ALLOWED_GROUP_IDS=...
```

Để bật Aurora fallback, thêm strong internal `AURORA_API_KEY`, chuẩn bị credential file local rồi start profile Aurora như mục 3.

Khởi động bot:

```bash
docker compose up -d --build
docker compose logs -f bot
```

---

## 7. Cấu hình AI + vision

```env
BAI_API_KEY=
BAI_TEXT_MODEL=qwen3.8-flash
BAI_REQUEST_TIMEOUT_SEC=30.0

AURORA_BASE_URL=http://aurora:8080/v1
AURORA_API_KEY=
AURORA_MODEL=auto
AURORA_REQUEST_TIMEOUT_SEC=90.0

TEXT_PROVIDER_ORDER=bai,aurora

VISION_ENABLED=1
BAI_VISION_MODEL=qwen3.8-flash
VISION_PROVIDER_ORDER=bai
MAX_IMAGES_PER_REQUEST=1
MAX_IMAGE_BYTES=8388608
MAX_TOTAL_IMAGE_BYTES=12582912
```

`VISION_ENABLED=0` chỉ tắt image understanding; text vẫn hoạt động. Aurora không tham gia vision.

Effective image limit là giới hạn nhỏ hơn giữa application limit và capability của các configured vision providers. B.AI advertise `max_images=1`, nên effective limit hiện tại là một ảnh.

---

## 8. Thêm AI provider mới

Một provider thông thường nên được add mà **không sửa** router state machine, orchestrator, stats, startup hoặc Telegram handlers:

1. Implement `AIProvider` trực tiếp, hoặc reuse `OpenAICompatProvider` nếu upstream dùng OpenAI-compatible Chat Completions.
2. Thêm provider-specific settings/credentials với namespace riêng.
3. Tạo provider-family factory trả về các configured route slots.
4. Register factory trong `PROVIDER_FACTORIES`.
5. Khai báo `ProviderCapabilities` cho từng slot và thêm provider key vào `TEXT_PROVIDER_ORDER` / `VISION_PROVIDER_ORDER` phù hợp.
6. Pass provider registry, ordered routing/fallback, portability và provider-specific regression tests.

Provider-specific payload mapping, response parsing, auth/header, reasoning metadata và compatibility quirks phải nằm trong adapter, không đưa vào core router.

Aurora trong PR này là implementation mẫu đầu tiên chứng minh registry của PR #38 có thể thêm provider mới mà không sửa `router.py`.

---

## 9. Nâng cấp deployment cũ

Không copy đè `.env` bằng `.env.example`. Sau `git pull`, backup rồi sync:

```bash
cp .env .env.bak
python scripts/sync_env.py
```

Script giữ value của key còn tồn tại và xóa key không còn trong `.env.example`.

Migration hiện tại:

- giữ B.AI settings hiện có;
- thêm Aurora keys mới từ `.env.example` nếu chưa có;
- giữ `TEXT_PROVIDER_ORDER` / `VISION_PROVIDER_ORDER` vì chúng là generic routing config;
- xóa credential/model variables của Gemini/Groq/OpenRouter/Cloudflare Workers AI vì chúng vẫn không còn trong template;
- giữ Telegram/search/Crawl4AI values nếu key còn tồn tại.

Nếu order cũ vẫn chứa provider đã bị xóa, ví dụ `bai,gemini`, `sync_env.py` giữ nguyên intent đó thay vì silently sửa; startup sẽ reject unknown provider rõ ràng. Trước khi restart production, order hợp lệ hiện tại là:

```env
TEXT_PROVIDER_ORDER=bai,aurora
VISION_PROVIDER_ORDER=bai
```

Aurora chưa có service key thì tự skip, nên có thể giữ default order trên deployment B.AI-only.

Chi tiết: [docs/ENV_SYNC.md](docs/ENV_SYNC.md).

---

## 10. Search / URL reading

Search không phụ thuộc provider registry.

```env
SEARCH_BACKEND=auto
SEARXNG_URL=http://searxng:8080
SEARXNG_TIMEOUT_SEC=7.0
DDGS_TIMEOUT_SEC=8.0
SEARCH_TOTAL_TIMEOUT_SEC=15.0
```

`auto` ưu tiên SearXNG rồi dùng DDGS theo policy resilience/cache hiện có.

Crawl4AI là optional URL rendering backend:

```env
CRAWL4AI_ENABLED=1
CRAWL4AI_URL=http://crawl4ai:11235
CRAWL4AI_API_TOKEN=
CRAWL4AI_TIMEOUT_SEC=25.0
CRAWL4AI_MAX_CHARS=12000
```

Nếu Crawl4AI không được cấu hình đầy đủ, URL reader degrade về generic reader thay vì làm bot fail startup.

---

## 11. Telegram usage và observability

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

Plain image không có text/caption trigger sẽ không tự gọi bot. Forum topic được cô lập history/lock bằng `(chat_id, message_thread_id)`.

`/status` hiển thị generic text/vision provider list, configured order, provider distribution, last provider, fallback transition count và cooldown/unavailable state. Khi chỉ B.AI được cấu hình, text configured list vẫn chỉ có B.AI dù default order có Aurora. Khi cả hai được cấu hình, fallback transition có thể hiển thị `bai -> aurora`.

---

## 12. Verification

```bash
python -m unittest discover -s tests -p 'test_aurora*.py' -v
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m pip check
python -m compileall -q app tests scripts
cp .env.example .env
mkdir -p aurora
touch aurora/session_tokens.txt
docker compose --profile aurora config --quiet
docker build --tag fcai-provider-test .
```

GitHub Actions chạy Python 3.11/3.12. Python 3.12 còn validate Compose/SearXNG YAML và build production image. CI không start Aurora và không dùng live ChatGPT credential.

Manual Aurora canary trước merge/deploy:

```bash
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode models
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode chat
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode tool
```

Sau đó exercise một controlled B.AI provider-local failure trong staging để xác nhận B.AI → Aurora fallback, tool round-trip và observability. Live canary là manual gate riêng; automated CI không chứng minh ChatGPT Web compatibility thực tế.

Rollback Aurora:

```env
TEXT_PROVIDER_ORDER=bai
```

```bash
docker compose --profile aurora stop aurora
```
