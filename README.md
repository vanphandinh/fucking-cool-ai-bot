# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.

**Current configured AI provider: B.AI.** B.AI là provider duy nhất đang được register trong production hiện tại; Gemini, Groq, OpenRouter và Cloudflare Workers AI đã bị xóa khỏi source/runtime.

**Architecture: generic capability-aware, health-aware, ordered multi-provider framework.** Core router, orchestrator, startup, metrics và Telegram handlers không coi B.AI là provider đặc biệt, nên có thể thêm provider khác sau này mà không viết lại state machine chính.

Bot vẫn giữ web search, image search, direct URL reading, X/Twitter reader, Crawl4AI, vision input và Telegram-native HTML formatting.

## Tài liệu đang duy trì

- [B.AI integration](docs/BAI_INTEGRATION.md) — model allowlist, adapter behavior, tool calling, health và synthesis.
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
          └─ current production registry: B.AI
               ├─ text slot
               └─ vision slot (max_images=1)
```

Provider contract là structural `AIProvider`; provider không bắt buộc kế thừa `OpenAICompatProvider`. `OpenAICompatProvider` chỉ là transport/parser reusable cho API kiểu OpenAI Chat Completions. B.AI hiện dùng implementation đó qua:

```text
POST https://api.b.ai/v1/chat/completions
```

Current production orders:

```env
TEXT_PROVIDER_ORDER=bai
VISION_PROVIDER_ORDER=bai
```

Framework hỗ trợ ordered fallback. Vì registry production hiện chỉ có `bai`, một B.AI outage hiện vẫn kết thúc request sau local recovery; không có provider thứ hai để thử. Khi provider mới được register và thêm vào order, router có thể chuyển sang provider đó theo cùng generic state machine.

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

## 3. Ordered fallback, tool state và Fresh Synthesis

Router chọn candidate theo route order, capability và health:

```text
configured order
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

Ví dụ `reasoning_content` của B.AI `mimo-v2.5` có thể replay trong cùng provider, nhưng không được leak sang provider khác.

Khi tool budget đóng/hết, router tạo **Fresh Synthesis** từ original request + bounded untrusted plain-text evidence, tắt tools và bỏ structured provider-local trajectory. Nếu provider hiện tại fail ở synthesis stage và còn provider sau, provider sau nhận cùng generic Fresh Synthesis context với tools vẫn tắt. Với B.AI, no-tool request còn đặt `tool_choice=none`.

Tool call phát sinh sau khi tools đã tắt không bao giờ được execute.

---

## 4. Cài đặt nhanh

Yêu cầu:

- Docker + Docker Compose plugin;
- Telegram bot token;
- credential của ít nhất một text provider trong configured order; hiện tại là B.AI API key;
- Python 3.12 nếu chạy helper scripts trực tiếp ngoài container.

```bash
git clone https://github.com/vanphandinh/fucking-cool-ai-bot.git
cd fucking-cool-ai-bot
cp .env.example .env
nano .env
```

Cấu hình tối thiểu hiện tại:

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
BAI_REQUEST_TIMEOUT_SEC=30.0
TEXT_PROVIDER_ORDER=bai

VISION_ENABLED=1
BAI_VISION_MODEL=qwen3.8-flash
VISION_PROVIDER_ORDER=bai
MAX_IMAGES_PER_REQUEST=1
MAX_IMAGE_BYTES=8388608
MAX_TOTAL_IMAGE_BYTES=12582912
```

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

Provider-specific payload mapping, response parsing, auth/header, reasoning metadata và compatibility quirks phải nằm trong adapter, không đưa vào core router.

---

## 7. Nâng cấp deployment cũ

Không copy đè `.env` bằng `.env.example`. Sau `git pull`, backup rồi sync:

```bash
cp .env .env.bak
python scripts/sync_env.py
```

Script giữ value của key còn tồn tại và xóa key không còn trong `.env.example`.

Migration hiện tại:

- giữ B.AI settings hiện có;
- giữ `TEXT_PROVIDER_ORDER` / `VISION_PROVIDER_ORDER` vì chúng vẫn là generic routing config;
- xóa credential/model variables của Gemini/Groq/OpenRouter/Cloudflare Workers AI vì chúng không còn trong template;
- giữ Telegram/search/Crawl4AI values nếu key còn tồn tại.

Nếu order cũ vẫn chứa provider đã bị xóa, ví dụ `bai,gemini`, `sync_env.py` **giữ nguyên intent đó** thay vì silently sửa. Startup sẽ reject unknown provider rõ ràng. Trước khi restart production, sửa order về các provider đang register; hiện tại:

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

Plain image không có text/caption trigger sẽ không tự gọi bot. Forum topic được cô lập history/lock bằng `(chat_id, message_thread_id)`.

Provider health state là per-slot:

- `401/403` → disable slot đến process restart;
- `429` → cooldown, ưu tiên numeric `Retry-After`;
- network/`5xx` transient → cooldown theo health policy;
- success → reset transient state.

`/status` hiển thị generic text/vision provider list, configured order, provider distribution, last provider, fallback transition count và cooldown/unavailable state. Trong B.AI-only production bình thường, fallback count thường là `0` vì chưa có provider thứ hai.

---

## 10. Verification

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m pip check
python -m compileall -q app tests scripts
cp .env.example .env
docker compose config --quiet
docker build --tag fcai-bai-generic-provider-test .
```

GitHub Actions chạy Python 3.11/3.12; Python 3.12 còn validate Compose/SearXNG YAML và build production image.

Manual production smoke sau deploy nên gồm:

1. text query không tool;
2. query cần web search;
3. direct URL;
4. một ảnh;
5. request vượt effective image limit;
6. `/status`;
7. B.AI failure path hiện tại;
8. nếu sau này có provider thứ hai, một controlled provider-local failure để xác nhận ordered fallback và metadata isolation.
