# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.
Bot hỗ trợ cả **text + image understanding**, có tool tìm web/ảnh và đọc trang, fallback AI theo capability,
và chỉ xử lý message trong phạm vi group được cấu hình.

Tài liệu đang duy trì:

- [Telegram vision input](docs/telegram-vision-input.md) — luồng ảnh, routing và giới hạn an toàn.
- [SearXNG production trên VPS](DEPLOY_SEARXNG_VPS.md) — profile SearXNG private trong Docker.

> `README.md`, `.env.example`, source code, Docker/CI là source of truth cho trạng thái hiện tại.
> Các kế hoạch/audit snapshot cũ được giữ trong Git/PR history thay vì duy trì thành tài liệu sống.

---

## 1. Khả năng hiện tại

- Chỉ hoạt động trong `group`/`supergroup` thuộc `ALLOWED_GROUP_IDS`.
- Có `LEARN_GROUP_ID_MODE` để lấy `chat_id`; khi không ở learn-mode bot tự rời chat lạ lúc được add.
- Trigger bằng `/ask`, `@mention`, hoặc reply trực tiếp vào tin của chính bot.
- Context hội thoại ngắn hạn giữ trong RAM theo `chat_id`; không có database.
- Text route free-tier-first, xếp theo `TEXT_PROVIDER_ORDER`; default: Groq → Cloudflare → OpenRouter → Gemini.
- Vision route tách riêng, chọn theo `VISION_PROVIDER_ORDER`; default: Groq Qwen 3.8 → Cloudflare Gemma 4 → Groq Qwen 3.6 → Gemini 3.8 Flash.
- Nhận Telegram photo hoặc JPEG/PNG/WebP gửi dạng document.
- Ở flow Telegram hiện tại, một request có thể lấy ảnh từ **message hiện tại + message được reply**
  (tối đa 2 ảnh thực tế). `MAX_IMAGES_PER_REQUEST` là trần capability/config, không tự thêm album support.
- Model có thể gọi `web_search`, `image_search` và `fetch_url`.
- Web search và image search dùng chung `SEARCH_BACKEND=auto|searxng|ddgs`; `auto` ưu tiên SearXNG rồi fallback DDGS.
- Image search trả ảnh trực tiếp qua Telegram; nếu full image URL lỗi sẽ thử thumbnail URL.
- `/status` cho admin hiển thị uptime, provider, fallback, search, lỗi gần nhất và provider cooldown.
- CI kiểm tra Python 3.11/3.12, lint, tests, dependency audit và Docker build.

---

## 2. Kiến trúc runtime

```text
Telegram group
   │
   ├─ allowlist / trigger / rate-limit
   │
   ├─ TelegramMediaLoader ──► UserRequest(text, quoted_text, ephemeral images)
   │
   ▼
Orchestrator
   │
   ├─ text request  ──► text-capable provider pool
   ├─ image request ──► vision-capable provider pool
   │
   └─ tools ──► web_search / image_search / fetch_url
                  │
                  └─ SEARCH_BACKEND = auto | searxng | ddgs
                         auto: SearXNG → DDGS
```

Provider router lọc theo capability trước khi fallback. Text request không rơi sang vision slot và image
request không rơi sang text-only slot.

---

## 3. Yêu cầu trước khi chạy

- Python runtime trong Docker: **3.12**.
- Docker + Docker Compose plugin trên VPS.
- Telegram bot token từ BotFather.
- Tối thiểu một text provider khả dụng: Groq, Cloudflare Workers AI, OpenRouter hoặc Gemini.
- Privacy Mode nên tắt nếu muốn bot đọc message/reply trong group theo workflow hiện tại.

Provider/model/rate-limit của dịch vụ bên thứ ba có thể thay đổi theo thời gian; repo chỉ đảm bảo các default
đang được cấu hình trong source và `.env.example`.

Default hiện tại được tối ưu theo hướng **free-tier-first**: ưu tiên model/router có thể dùng trên free tier và
fallback qua provider độc lập khi quota hoặc provider lỗi. Đây **không phải cơ chế cưỡng chế billing**. Bot không
thể phát hiện account/project của Groq, Cloudflare hoặc Gemini đang ở Free hay Paid tier; nếu credential thuộc
paid tier thì provider vẫn có thể tính phí theo chính sách của họ. Muốn vận hành thực tế ở $0, hãy giữ từng
provider trên free tier/quota phù hợp và theo dõi billing ở dashboard của provider.

---

## 4. Cài đặt nhanh

```bash
git clone https://github.com/vanphandinh/fucking-cool-ai-bot.git
cd fucking-cool-ai-bot

cp .env.example .env
nano .env

# tối thiểu điền:
# BOT_TOKEN=...
# GROQ_API_KEY=...   # hoặc Cloudflare / OpenRouter / Gemini
# ADMIN_IDS=...
# ALLOWED_GROUP_IDS=...  # hoặc dùng learn-mode ở mục 5

docker compose up -d --build
docker compose logs -f bot
```

Nếu cấu hình sai `SEARCH_BACKEND`, `LOG_LEVEL`, timeout hoặc các giá trị số có constraint, app sẽ fail-fast
khi load `Settings` thay vì chạy với cấu hình mơ hồ.

### Nâng cấp deployment đã có `.env`

`Settings` đọc `.env` và biến môi trường **ưu tiên hơn default trong source**. Vì vậy chỉ `git pull` sẽ không tự
thay các giá trị model/order cũ đã có trong `.env`. Sau khi nâng cấp, hãy đối chiếu `.env.example` và cập nhật
ít nhất các dòng sau nếu muốn dùng default hiện tại:

```env
GROQ_MODEL=openai/gpt-oss-120b
OPENROUTER_MODEL=openrouter/free
GEMINI_MODEL=gemini-3.8-flash
CLOUDFLARE_TEXT_MODEL=@cf/zai-org/glm-4.7-flash
TEXT_PROVIDER_ORDER=groq,cloudflare,openrouter,gemini
VISION_PROVIDER_ORDER=groq_qwen38,cloudflare,groq_qwen36,gemini
SEARCH_BACKEND=auto
IMAGE_SEARCH_MAX_RESULTS=4
MAX_CONTEXT_TURNS=6
MAX_TOOL_ROUNDS=2
```

Nếu `.env` cũ còn `TAVILY_API_KEY` hoặc `IMAGE_SEARCH_BACKEND`, có thể xóa: runtime mới không dùng hai biến này.
Không copy đè secret từ `.env.example`; chỉ cập nhật các key cần thiết rồi rebuild/restart bot.

---

## 5. Lấy `chat_id` và allowlist

Khi chưa biết `chat_id`:

```env
LEARN_GROUP_ID_MODE=1
ALLOWED_GROUP_IDS=
```

Khởi động bot rồi xem log:

```bash
docker compose up -d --build
docker compose logs -f bot
# GROUP_ID_LEARN: chat_id=-1001234567890 ...
```

Sau đó khóa lại phạm vi:

```env
LEARN_GROUP_ID_MODE=0
ALLOWED_GROUP_IDS=-1001234567890
```

Có thể cấu hình nhiều group bằng danh sách phân tách dấu phẩy.

---

## 6. Cách dùng

| Flow | Ví dụ / hành vi |
|---|---|
| `/ask` | `/ask giải thích blockchain ngắn gọn` |
| Mention | `@FuckingCoolAIbot giá vàng hôm nay?` |
| Tìm ảnh | `@FuckingCoolAIbot tìm cho tôi 4 ảnh capybara` |
| Reply bot | Reply vào tin của bot rồi nhập câu hỏi; không bắt buộc mention lại |
| Reply thành viên | Cần `@mention` hoặc dùng `/ask` để trigger |
| Ảnh hiện tại | Telegram photo hoặc JPEG/PNG/WebP document + caption trigger bot |
| Ảnh được reply | Reply ảnh rồi dùng `@mention` hoặc `/ask <câu hỏi>` |
| `/help` | Hướng dẫn ngắn trong Telegram |
| `/status` | Chỉ admin trong `ADMIN_IDS` |

Plain image không có text/caption trigger sẽ không tự gọi bot.

### Vision hiện hỗ trợ gì?

- Telegram photo được coi là `image/jpeg`.
- Document chỉ nhận `image/jpeg`, `image/png`, `image/webp`.
- Loader lấy ảnh ở message hiện tại và/hoặc message được reply.
- Raw image bytes chỉ tồn tại trong request RAM; khi lưu ChatMemory chỉ lưu marker dạng
  `[kèm N ảnh] <câu hỏi>`, không lưu bytes/base64.
- Base64 data URL chỉ được tạo tại provider boundary.
- Provider error excerpt được redact image data URL trước khi có thể đi vào log/status.

Chi tiết: [docs/telegram-vision-input.md](docs/telegram-vision-input.md).

---

## 7. Cấu hình

### 7.1 Telegram

| Biến | Default | Ý nghĩa |
|---|---|---|
| `BOT_TOKEN` | trống | Bắt buộc để chạy |
| `BOT_USERNAME` | `FuckingCoolAIbot` | Fallback; startup gọi Telegram `getMe()` và đồng bộ username thực |
| `ALLOWED_GROUP_IDS` | trống | Danh sách group/supergroup được phép |
| `ADMIN_IDS` | trống | User ID được dùng `/status` |
| `LEARN_GROUP_ID_MODE` | `0` | Log `chat_id` để bootstrap allowlist |

### 7.2 Text provider pool

| Biến | Default model | Vai trò |
|---|---|---|
| `GROQ_API_KEY` / `GROQ_MODEL` | `openai/gpt-oss-120b` | Free-tier-first text primary |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_TEXT_MODEL` | `@cf/zai-org/glm-4.7-flash` | Cloudflare text fallback |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | `openrouter/free` | Free-model router fallback |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | `gemini-3.8-flash` | Chất lượng cao, giữ free-tier quota làm reserve |
| `TEXT_PROVIDER_ORDER` | `groq,cloudflare,openrouter,gemini` | Thứ tự fallback text hiệu lực |

Startup yêu cầu **ít nhất một** text provider khả dụng. `configured_provider_names` chỉ báo provider vừa có
credential/model phù hợp vừa nằm trong `TEXT_PROVIDER_ORDER`.

Các model/router mặc định có thể dùng theo free tier tương ứng, nhưng source không biết billing plan của API
key. `openrouter/free` là router miễn phí; với Groq/Cloudflare/Gemini, account/project ở paid tier vẫn có thể
phát sinh phí theo policy của provider.

### 7.3 Vision provider pool

| Biến | Default | Ý nghĩa |
|---|---|---|
| `VISION_ENABLED` | `1` | Bật/tắt toàn bộ vision slot; text route không bị ảnh hưởng |
| `GEMINI_VISION_MODEL` | `gemini-3.8-flash` | Model của Gemini vision slot |
| `GROQ_VISION_MODELS` | `qwen/qwen3.8-27b,qwen/qwen3.6-27b` | Tối đa hai Groq vision slot theo thứ tự list |
| `CLOUDFLARE_ACCOUNT_ID` | trống | Bắt buộc cùng API token để tạo Cloudflare slot |
| `CLOUDFLARE_API_TOKEN` | trống | Token Workers AI dùng chung text/vision |
| `CLOUDFLARE_VISION_MODEL` | `@cf/google/gemma-4-26b-a4b-it` | Cloudflare vision model |
| `VISION_PROVIDER_ORDER` | `groq_qwen38,cloudflare,groq_qwen36,gemini` | Thứ tự fallback vision hiệu lực |
| `MAX_IMAGES_PER_REQUEST` | `3` | Capability/config ceiling; Telegram loader hiện chỉ cung cấp current + reply |
| `MAX_IMAGE_BYTES` | `8388608` | Trần bytes cho từng ảnh |
| `MAX_TOTAL_IMAGE_BYTES` | `12582912` | Trần tổng bytes ảnh trong một request |

`configured_vision_provider_names` chỉ báo các slot vừa có credential/model phù hợp vừa nằm trong
`VISION_PROVIDER_ORDER`.

Cloudflare được đặt giữa hai Groq vision slot để tăng provider diversity: quota/auth/outage Groq không khiến
router thử hai model cùng provider liên tiếp trước khi đổi sang provider độc lập.

### 7.4 Web + image search

| Biến | Default | Ý nghĩa |
|---|---|---|
| `SEARCH_BACKEND` | `auto` | Dùng chung cho web + image search: `auto`, `searxng`, `ddgs` |
| `SEARXNG_URL` | `http://searxng:8080` trong `.env.example` | URL JSON API của SearXNG self-hosted |
| `IMAGE_SEARCH_MAX_RESULTS` | `4` | Số ảnh tối đa hệ thống trả về, giới hạn 1-8 |

Routing `auto`:

```text
web_search:   SearXNG (nếu có URL) → DDGS
image_search: SearXNG Images (nếu có URL) → DDGS Images
```

Fallback xảy ra khi SearXNG lỗi hoặc không có kết quả usable. Chọn `searxng` hoặc `ddgs` sẽ khóa vào đúng
backend đó và không tự chuyển backend khi lỗi. Tavily đã bị loại khỏi runtime để giữ search stack miễn phí,
đơn giản và không cần thêm API key.

Các biến Compose-only cho SearXNG:

- `SEARXNG_IMAGE`
- `GRANIAN_BLOCKING_THREADS` (default `1`)
- `GRANIAN_BACKPRESSURE` (default `2`)

### 7.5 Limits / runtime

| Biến | Default | Ý nghĩa |
|---|---|---|
| `MAX_QUESTIONS_PER_MIN_PER_USER` | `3` | Rate-limit RAM theo user; `0` = chặn toàn bộ câu hỏi |
| `MAX_CONTEXT_TURNS` | `6` | Số cặp hỏi/đáp gần nhất giữ theo chat; giảm token free-tier |
| `MAX_TOOL_ROUNDS` | `2` | Số vòng tool tối đa trong completion; giảm số request/token |
| `REQUEST_TIMEOUT_SEC` | `60.0` | Timeout HTTP/provider/reader |
| `QUESTION_TIMEOUT_SEC` | `180.0` | Deadline tổng của một câu hỏi, gồm chờ chat lock |
| `LOG_LEVEL` | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG` (`WARN` được normalize) |

Router còn có safety cap nội bộ tối đa 8 tool calls cho một completion, dùng chung qua retry/fallback.

---

## 8. Routing, fallback và provider health

### Text

Provider được tạo từ credential/model hiện có rồi xếp theo `TEXT_PROVIDER_ORDER`.
Default:

```text
Groq / openai/gpt-oss-120b
  -> Cloudflare / @cf/zai-org/glm-4.7-flash
  -> OpenRouter / openrouter/free
  -> Gemini / gemini-3.8-flash
```

### Vision

Vision slot được tạo từ credential hiện có rồi xếp theo `VISION_PROVIDER_ORDER`.
Default:

```text
Groq Qwen 3.8
  -> Cloudflare Gemma 4
  -> Groq Qwen 3.6
  -> Gemini 3.8 Flash
```

Provider health là state trong RAM:

- `401/403`: disable slot đến khi process restart.
- `429`: cooldown theo numeric `Retry-After`; nếu không parse được thì mặc định 60 giây.
- Network/`5xx` transient: sau 2 lỗi liên tiếp thì cooldown 30 giây.
- Thành công reset transient counter/cooldown.

Fallback giữ chung tool budget của request; provider không hỗ trợ tools có thể retry plain mode theo error
classification thay vì làm hỏng toàn bộ route.

### Metadata khi tool-calling

Một số model yêu cầu metadata từ assistant response phải được gửi lại nguyên vẹn ở tool turn kế tiếp. Adapter
hiện xử lý hai nhóm đã được audit:

- Gemini 3.x: giữ `tool_calls[].extra_content.google.thought_signature` trong cùng provider;
- OpenRouter/reasoning models: giữ assistant-level `reasoning_details`, `reasoning` và `reasoning_content`.

Các metadata provider-specific này được loại bỏ khi chuyển transcript sang provider fallback khác để tránh
provider kế từ chối unknown field. Tool budget và transcript tool result vẫn được giữ qua fallback.

---

## 9. Web/image search và đọc trang

Orchestrator expose ba tool:

- `web_search(query)` — tìm thông tin web bằng backend đang chọn.
- `image_search(query)` — tìm ảnh Internet bằng cùng `SEARCH_BACKEND`.
- `fetch_url(url)` — đọc một URL cụ thể.

`fetch_url` chỉ chấp nhận destination public; reader kiểm tra URL/DNS/redirect để chặn localhost, private
network và các address class không được hỗ trợ. Reader cũng giới hạn body/deadline và từ chối compressed
response trong đường đọc trực tiếp để tránh memory amplification.

Image result được gửi cho Telegram bằng remote HTTP URL để bot server không phải tải ảnh tùy ý. Nếu URL ảnh
đầy đủ bị Telegram từ chối, sender thử thumbnail URL; một ảnh lỗi không làm hỏng text answer hoặc các ảnh khác.

### SearXNG private

Profile `searxng` không publish port ra host. Bot gọi nội bộ `http://searxng:8080`.
Trước khi bật profile phải tạo file thật:

```bash
cp -i searxng/settings.example.yml searxng/settings.yml
openssl rand -hex 32
nano searxng/settings.yml   # thay secret_key

docker compose --profile searxng up -d --build
```

Hướng dẫn vận hành/nâng cấp: [DEPLOY_SEARXNG_VPS.md](DEPLOY_SEARXNG_VPS.md).

---

## 10. Security và dữ liệu

- `.env` và `searxng/settings.yml` chứa secret và đã nằm trong `.gitignore`.
- Docker image chạy bằng user non-root (`appuser`).
- Bot không có database; context, stats, rate-limit và provider health đều là in-memory state.
- Image bytes không được ghi vào ChatMemory và không persist bởi app.
- Nội dung user/ảnh vẫn phải được gửi tới AI provider được chọn để model xử lý; không gửi dữ liệu nhạy cảm
  nếu policy của provider/tổ chức không cho phép.
- Web reader có SSRF guard và không dùng URL nội bộ/localhost làm tool target.
- Search-result image URL không được bot server tải xuống; Telegram fetch remote URL trực tiếp.
- Startup giữ pending Telegram updates (`drop_pending_updates=False`); shutdown hủy/join handler đang chạy
  trước khi đóng Telegram/provider clients.

---

## 11. Vận hành

```bash
docker compose ps
docker compose logs --tail=100 bot
docker compose restart bot
docker compose up -d --build
```

Nếu dùng SearXNG:

```bash
docker compose --profile searxng ps
docker compose logs --tail=100 searxng
docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/healthz
```

Các lỗi startup đáng chú ý:

- thiếu `BOT_TOKEN` → dừng.
- không có text provider khả dụng trong `TEXT_PROVIDER_ORDER` → dừng.
- có Cloudflare token nhưng thiếu account ID → warning; bỏ qua Cloudflare text/vision.
- vision bật nhưng không có vision provider hợp lệ → warning; text bot vẫn chạy.
- `SEARCH_BACKEND=searxng` nhưng `SEARXNG_URL` trống → search tool sẽ lỗi khi được gọi.
- `SEARCH_BACKEND=auto` với `SEARXNG_URL` trống → dùng DDGS trực tiếp.

---

## 12. Tests và CI

Cài dependency ứng dụng và tool kiểm tra giống CI:

```bash
python -m pip install -r requirements.txt
python -m pip install ruff==0.16.6 pip-audit==2.10.1
```

Chạy full verification:

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m compileall -q app tests
python -m pip check
python -m pip_audit --progress-spinner off
```

Workflow [Audit checks](.github/workflows/audit.yml) chạy khi push, pull request hoặc `workflow_dispatch`:

- Python 3.11 + 3.12.
- Ruff, `pip check`, `compileall`.
- Cả hai test suites.
- `pip-audit`.
- Production Docker build trên Python 3.12 job.

`tests/test_search_backend_auto.py` khóa unified search policy, auto fallback và việc loại Tavily.
`tests/test_image_search.py` khóa image normalization, image fallback, tool payload và Telegram delivery.
`tests/test_free_routing.py` khóa free-tier-first model defaults, text provider order, Cloudflare text slot,
provider-diverse vision order, Gemini thought-signature replay và cross-provider metadata isolation.
`tests/test_provider_metadata.py` khóa OpenRouter reasoning metadata replay. Tests dùng fake Telegram/provider
transports và local fixtures; CI không chứng minh live credential/provider E2E trong môi trường production.

---

## 13. Cấu trúc repo

```text
app/
├── main.py                # startup, polling, cleanup
├── config.py              # pydantic-settings
├── ai/
│   ├── base.py            # OpenAI-compatible transport + provider errors
│   ├── capabilities.py    # text/vision capability metadata
│   ├── health.py          # cooldown/disable state
│   ├── router.py          # capability-aware fallback + tool budget
│   ├── multimodal.py      # image bytes -> data URL tại provider boundary
│   └── gemini.py / groq.py / openrouter.py / cloudflare.py
├── bot/
│   ├── filters.py         # allowlist + trigger
│   ├── handlers.py        # /ask, /help, /status, lifecycle
│   ├── image_results.py   # Telegram image-search delivery
│   └── media.py           # Telegram image validation/download bounds
├── core/                  # request, orchestrator, memory, rate-limit, stats, formatting
└── search/                # ddgs/searxng search + safe page reader

tests/
├── run_tests.py
├── test_audit_regressions.py
├── test_free_routing.py
├── test_image_search.py
├── test_search_backend_auto.py
├── test_provider_metadata.py
└── test_vision.py

.github/workflows/audit.yml
Dockerfile
docker-compose.yml
.env.example
searxng/settings.example.yml
searxng/limiter.toml
docs/telegram-vision-input.md
DEPLOY_SEARXNG_VPS.md
```