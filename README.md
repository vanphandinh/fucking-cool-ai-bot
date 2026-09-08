# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.
Bot hỗ trợ **text + image understanding**, tìm web/ảnh, đọc URL trực tiếp, đọc X/Twitter status/thread theo
luồng free-first, fallback AI theo capability và chỉ xử lý message trong phạm vi group được cấu hình.

## Tài liệu đang duy trì

- [Telegram vision input](docs/telegram-vision-input.md) — luồng ảnh, routing và giới hạn an toàn.
- [Telegram-native formatting](docs/TELEGRAM_FORMATTING.md) — HTML sanitizer, splitter và fallback plain text.
- [X/Twitter content fetching](docs/X_CONTENT_FETCHING.md) — FxTwitter/oEmbed/generic fallback, safety và rollback.
- [SearXNG production trên VPS](DEPLOY_SEARXNG_VPS.md) — profile SearXNG private trong Docker.
- [SearXNG DuckDuckGo incident 2026-09-08](docs/SEARXNG_DDG_INCIDENT_2026-09-08.md) — incident note và workaround.

> `README.md`, `.env.example`, source code, Docker/CI và các guide runtime ở trên là source of truth.
> `docs/superpowers/plans/` là snapshot kế hoạch lịch sử, không phải tài liệu vận hành cần đồng bộ sau mỗi PR.

---

## 1. Khả năng hiện tại

- Chỉ hoạt động trong `group`/`supergroup` thuộc `ALLOWED_GROUP_IDS`.
- Có `LEARN_GROUP_ID_MODE` để lấy `chat_id`; ngoài learn-mode bot tự rời chat lạ khi được add.
- Trigger bằng `/ask`, `@mention`, hoặc reply trực tiếp vào tin của chính bot.
- Context hội thoại ngắn hạn giữ trong RAM theo `chat_id`; không có database.
- Text route free-tier-first theo `TEXT_PROVIDER_ORDER`; default: Groq → Cloudflare → OpenRouter → Gemini.
- Vision route tách riêng theo `VISION_PROVIDER_ORDER`; default: Groq Qwen 3.8 → Cloudflare Gemma 4 → Groq Qwen 3.6 → Gemini 3.8 Flash.
- Nhận Telegram photo hoặc JPEG/PNG/WebP gửi dạng document.
- Flow Telegram hiện tại có thể lấy ảnh từ **message hiện tại + message được reply** (tối đa 2 ảnh thực tế).
  `MAX_IMAGES_PER_REQUEST=3` là trần capability/config, không tự triển khai album aggregation.
- Model có ba tool: `web_search`, `image_search`, `fetch_url`.
- `fetch_url` nhận `mode=auto|x_thread`; URL X/Twitter status được ưu tiên đọc trực tiếp thay vì search URL.
- Direct X/Twitter status dùng FxTwitter API v2 → X oEmbed → generic SSRF-safe reader; không cần API key.
- Web/image search dùng `SEARCH_BACKEND=auto|searxng|ddgs`; `auto` ưu tiên SearXNG rồi fallback DDGS.
- Image search trả ảnh trực tiếp qua Telegram; full image URL lỗi sẽ thử thumbnail URL.
- Answer chính được render bằng Telegram-native HTML có sanitizer/splitter; memory lưu bản plain text.
- `/status` cho admin hiển thị uptime, provider, fallback, search, lỗi gần nhất và provider cooldown.
- CI kiểm tra Python 3.11/3.12, lint/compile, tests, dependency audit; job 3.12 còn validate SearXNG YAML và build production image.

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
   └─ tools
       ├─ web_search ─────► SEARCH_BACKEND = auto | searxng | ddgs
       │                     auto: SearXNG → DDGS
       ├─ image_search ───► cùng SEARCH_BACKEND
       └─ fetch_url
            ├─ X/Twitter status + X_FETCH_ENABLED=1
            │    └─ FxTwitter v2 → X oEmbed → generic reader
            └─ URL khác / X_FETCH_ENABLED=0
                 └─ generic SSRF-safe reader (Jina → direct HTML)
```

Provider router lọc theo capability trước khi fallback. Tool budget dùng chung qua provider fallback. Với URL
http/https cụ thể do user cung cấp và yêu cầu đọc nội dung, system policy ưu tiên `fetch_url` trước
`web_search`; search chỉ nên dùng khi direct fetch không đủ hoặc user cần kiểm chứng/tìm nguồn ngoài URL đó.

---

## 3. Yêu cầu trước khi chạy

- Python runtime trong Docker: **3.12**.
- Docker + Docker Compose plugin trên VPS.
- Telegram bot token từ BotFather.
- Tối thiểu một text provider khả dụng: Groq, Cloudflare Workers AI, OpenRouter hoặc Gemini.
- Privacy Mode nên tắt nếu muốn bot đọc message/reply trong group theo workflow hiện tại.

Provider/model/rate-limit của dịch vụ bên thứ ba có thể thay đổi theo thời gian; repo chỉ đảm bảo default đang
được cấu hình trong source và `.env.example`.

Default hiện tại tối ưu theo hướng **free-tier-first**, nhưng đây không phải cơ chế cưỡng chế billing. Bot không
biết account/project Groq, Cloudflare hoặc Gemini đang ở Free hay Paid tier. Muốn vận hành thực tế ở $0, phải
giữ credential trên free tier/quota phù hợp và theo dõi billing ở dashboard provider.

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

Nếu cấu hình sai `SEARCH_BACKEND`, `LOG_LEVEL`, timeout hoặc giá trị số có constraint, app fail-fast khi load
`Settings` thay vì chạy với cấu hình mơ hồ.

### Nâng cấp deployment đã có `.env`

`.env`/environment **ưu tiên hơn default trong source**, nên `git pull` không tự thay các giá trị cũ. Sau khi
nâng cấp, đối chiếu `.env.example` và cập nhật ít nhất các default cần thiết:

```env
GROQ_MODEL=openai/gpt-oss-120b
OPENROUTER_MODEL=openrouter/free
GEMINI_MODEL=gemini-3.8-flash
CLOUDFLARE_TEXT_MODEL=@cf/zai-org/glm-4.7-flash
TEXT_PROVIDER_ORDER=groq,cloudflare,openrouter,gemini
VISION_PROVIDER_ORDER=groq_qwen38,cloudflare,groq_qwen36,gemini
SEARCH_BACKEND=auto
IMAGE_SEARCH_MAX_RESULTS=4
X_FETCH_ENABLED=1
MAX_CONTEXT_TURNS=6
MAX_TOOL_ROUNDS=2
```

`X_FETCH_ENABLED=1` không cần API key. Set `0` nếu muốn rollback specialized X reader và đưa X URL về generic
reader. Không tăng `MAX_TOOL_ROUNDS` để chữa fetch/search failure; production baseline của repo là `2`.

Nếu `.env` cũ còn `TAVILY_API_KEY` hoặc `IMAGE_SEARCH_BACKEND`, có thể xóa: runtime hiện tại không dùng hai biến
này. Không copy đè secret từ `.env.example`; chỉ cập nhật key cần thiết rồi rebuild/recreate bot.

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
| Đọc URL | Gửi URL + yêu cầu đọc/tóm tắt; AI ưu tiên `fetch_url` trước search |
| Đọc X post | Gửi `https://x.com/<user>/status/<id>` + câu hỏi/tóm tắt |
| Đọc X thread | Yêu cầu đọc toàn bộ thread; AI có thể gọi `fetch_url(..., mode="x_thread")` |
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
- Raw image bytes chỉ tồn tại trong request RAM; ChatMemory chỉ lưu marker `[kèm N ảnh] <câu hỏi>`.
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
| `GEMINI_API_KEY` / `GEMINI_MODEL` | `gemini-3.8-flash` | Reserve chất lượng cao |
| `TEXT_PROVIDER_ORDER` | `groq,cloudflare,openrouter,gemini` | Thứ tự fallback text hiệu lực |

Startup yêu cầu **ít nhất một** text provider khả dụng. `configured_provider_names` chỉ báo provider vừa có
credential/model phù hợp vừa nằm trong `TEXT_PROVIDER_ORDER`.

### 7.3 Vision provider pool

| Biến | Default | Ý nghĩa |
|---|---|---|
| `VISION_ENABLED` | `1` | Bật/tắt toàn bộ vision slot; text route không bị ảnh hưởng |
| `GEMINI_VISION_MODEL` | `gemini-3.8-flash` | Gemini vision slot |
| `GROQ_VISION_MODELS` | `qwen/qwen3.8-27b,qwen/qwen3.6-27b` | Tối đa hai Groq vision slot |
| `CLOUDFLARE_VISION_MODEL` | `@cf/google/gemma-4-26b-a4b-it` | Cloudflare vision model |
| `VISION_PROVIDER_ORDER` | `groq_qwen38,cloudflare,groq_qwen36,gemini` | Thứ tự fallback vision hiệu lực |
| `MAX_IMAGES_PER_REQUEST` | `3` | Capability/config ceiling |
| `MAX_IMAGE_BYTES` | `8388608` | Trần bytes từng ảnh |
| `MAX_TOTAL_IMAGE_BYTES` | `12582912` | Trần tổng bytes ảnh/request |

Cloudflare nằm giữa hai Groq vision slot để tăng provider diversity trước khi thử model Groq thứ hai.

### 7.4 Web + image search

| Biến | Default | Ý nghĩa |
|---|---|---|
| `SEARCH_BACKEND` | `auto` | `auto`, `searxng`, `ddgs` |
| `SEARXNG_URL` | `http://searxng:8080` trong `.env.example` | JSON API của SearXNG self-hosted |
| `IMAGE_SEARCH_MAX_RESULTS` | `4` | Số ảnh tối đa, giới hạn 1-8 |

Routing `auto`:

```text
web_search:   SearXNG (nếu có URL) → DDGS
image_search: SearXNG Images (nếu có URL) → DDGS Images
```

Fallback xảy ra khi SearXNG lỗi hoặc không có kết quả usable. Chọn `searxng` hoặc `ddgs` khóa vào backend đó.
Tavily đã bị loại khỏi runtime để giữ search stack miễn phí và không cần thêm API key.

### 7.5 Direct URL / X content

| Biến | Default | Ý nghĩa |
|---|---|---|
| `X_FETCH_ENABLED` | `1` | Bật specialized reader cho public X/Twitter status URL; `0` = generic reader only |
| `REQUEST_TIMEOUT_SEC` | `60.0` | Timeout reader/provider; specialized X resolver tự giới hạn tổng deadline tối đa 12 giây |

`fetch_url` nhận:

```text
fetch_url(url, mode="auto")      # URL thường hoặc focal X status
fetch_url(url, mode="x_thread") # chỉ hợp lệ cho X/Twitter status URL
```

Supported specialized status hosts: `x.com`, `twitter.com`, mobile variants, `fxtwitter.com`, `fixupx.com`.
Chỉ path có status ID số hợp lệ mới được canonicalize về `https://x.com/.../status/<id>`. Host giả như
`x.com.evil.example` không được nhận là X.

X resolver:

```text
mode=auto:
  FxTwitter /2/status/{id}
    → X publish.oEmbed
    → generic reader

mode=x_thread:
  FxTwitter /2/thread/{id}
    → FxTwitter focal /2/status/{id}
    → X publish.oEmbed
    → generic reader
```

Tool payload X tối đa 5500 ký tự; thread tối đa 12 post. Raw media CDN URL không được đưa vào LLM context;
media type/alt text vẫn được giữ khi có. Duplicate `(url, mode)` trong cùng một câu hỏi dùng request-local
cache để không fetch lặp.

Chi tiết: [docs/X_CONTENT_FETCHING.md](docs/X_CONTENT_FETCHING.md).

### 7.6 Limits / runtime

| Biến | Default | Ý nghĩa |
|---|---|---|
| `MAX_QUESTIONS_PER_MIN_PER_USER` | `3` | Rate-limit RAM theo user; `0` = chặn toàn bộ câu hỏi |
| `MAX_CONTEXT_TURNS` | `6` | Số cặp hỏi/đáp gần nhất giữ theo chat |
| `MAX_TOOL_ROUNDS` | `2` | Số vòng tool tối đa; production baseline để tiết kiệm token |
| `REQUEST_TIMEOUT_SEC` | `60.0` | Timeout HTTP/provider/reader |
| `QUESTION_TIMEOUT_SEC` | `180.0` | Deadline tổng của một câu hỏi, gồm chờ chat lock |
| `LOG_LEVEL` | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG`; `WARN` được normalize |

Router còn có safety cap nội bộ tối đa 8 tool calls cho một completion, dùng chung qua retry/fallback.

---

## 8. Routing, fallback và provider health

### Text

```text
Groq / openai/gpt-oss-120b
  → Cloudflare / @cf/zai-org/glm-4.7-flash
  → OpenRouter / openrouter/free
  → Gemini / gemini-3.8-flash
```

### Vision

```text
Groq Qwen 3.8
  → Cloudflare Gemma 4
  → Groq Qwen 3.6
  → Gemini 3.8 Flash
```

Provider health là state trong RAM:

- `401/403`: disable slot đến process restart.
- `429`: cooldown theo numeric `Retry-After`; nếu không parse được thì mặc định 60 giây.
- Network/`5xx` transient: sau 2 lỗi liên tiếp cooldown 30 giây.
- Thành công reset transient counter/cooldown.

Fallback giữ chung tool budget của request. Provider không hỗ trợ tools có thể retry plain mode theo error
classification thay vì làm hỏng toàn bộ route.

### Metadata khi tool-calling

Adapter hiện round-trip metadata bắt buộc trong **cùng provider**, rồi strip trước cross-provider fallback:

- Gemini 3.x: `tool_calls[].extra_content.google.thought_signature`;
- OpenRouter/reasoning models: `reasoning_details`, `reasoning`, `reasoning_content`.

Tool result/transcript portable vẫn được giữ qua fallback.

---

## 9. Web/image search và đọc URL

Orchestrator expose đúng ba tool:

- `web_search(query)` — tìm thông tin web bằng backend đang chọn.
- `image_search(query)` — tìm ảnh Internet bằng cùng `SEARCH_BACKEND`.
- `fetch_url(url, mode?)` — đọc một URL cụ thể; `mode` mặc định `auto`, có thêm `x_thread` cho X status.

### Quy tắc direct URL

Khi user đã cung cấp URL cụ thể và hỏi nội dung URL đó, AI được hướng dẫn gọi `fetch_url` **trước**
`web_search`. Với X URL đã fetch thành công và đủ dữ liệu, prompt cũng yêu cầu không tự tạo vòng search mirror
qua fxtwitter/nitter/fixupx hoặc exact quote. Mục tiêu là tránh search fan-out, giảm latency và tránh đốt quota
model/tool rounds.

`fetch_url` generic chỉ chấp nhận destination public; reader kiểm tra URL/DNS/redirect để chặn localhost,
private network và address class không hỗ trợ. Reader giới hạn body/deadline và từ chối compressed response
trên direct-download path để giảm memory amplification.

Specialized X upstream host được cố định trong code (`api.fxtwitter.com`, `publish.x.com`), không lấy host API
từ user input. Source hiển thị cho user vẫn là canonical `x.com`, không phải mirror/API URL.

### Image result delivery

Image result được gửi cho Telegram bằng remote HTTP URL để bot server không phải tải ảnh search-result tùy ý.
Nếu full URL bị Telegram từ chối, sender thử thumbnail URL; một ảnh lỗi không làm hỏng text answer hay ảnh khác.

### SearXNG private

Profile `searxng` không publish port ra host. Bot gọi nội bộ `http://searxng:8080`.

```bash
cp -i searxng/settings.example.yml searxng/settings.yml
openssl rand -hex 32
nano searxng/settings.yml   # thay secret_key

docker compose --profile searxng up -d --build
```

Hướng dẫn vận hành/nâng cấp: [DEPLOY_SEARXNG_VPS.md](DEPLOY_SEARXNG_VPS.md).

---

## 10. Security và dữ liệu

- `.env` và `searxng/settings.yml` chứa secret và nằm trong `.gitignore`.
- Docker image chạy bằng user non-root (`appuser`).
- Bot không có database; context, stats, rate-limit và provider health là in-memory state.
- Image bytes không được ghi vào ChatMemory và không persist bởi app.
- Nội dung user/ảnh vẫn phải gửi tới AI provider được chọn để model xử lý.
- Generic web reader có SSRF guard và không dùng URL nội bộ/localhost làm tool target.
- X specialized parser chỉ nhận allowlisted host + numeric status ID; API egress đi tới host cố định trong code.
- X media CDN URL bị loại khỏi tool text; chỉ media type/alt text được giữ khi có.
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

Kiểm tra effective config quan trọng sau upgrade `.env`:

```bash
docker compose exec -T bot python - <<'PY'
from app.config import Settings
s = Settings()
print("SEARCH_BACKEND    =", s.search_backend)
print("X_FETCH_ENABLED   =", s.x_fetch_enabled)
print("MAX_CONTEXT_TURNS =", s.max_context_turns)
print("MAX_TOOL_ROUNDS   =", s.max_tool_rounds)
print("REQUEST_TIMEOUT   =", s.request_timeout_sec)
print("QUESTION_TIMEOUT  =", s.question_timeout_sec)
PY
```

Kỳ vọng free-tier-first production baseline: `X_FETCH_ENABLED=True`, `MAX_TOOL_ROUNDS=2`.

Nếu dùng SearXNG:

```bash
docker compose --profile searxng ps
docker compose logs --tail=100 searxng
docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/healthz
```

Lỗi startup đáng chú ý:

- thiếu `BOT_TOKEN` → dừng.
- không có text provider khả dụng trong `TEXT_PROVIDER_ORDER` → dừng.
- có Cloudflare token nhưng thiếu account ID → warning; bỏ qua Cloudflare text/vision.
- vision bật nhưng không có vision provider hợp lệ → warning; text bot vẫn chạy.
- `SEARCH_BACKEND=searxng` nhưng `SEARXNG_URL` trống → search tool lỗi khi được gọi.
- `SEARCH_BACKEND=auto` với `SEARXNG_URL` trống → dùng DDGS trực tiếp.
- X specialized resolver lỗi → tự thử oEmbed/generic reader; không yêu cầu SearXNG phải sửa để đọc direct X URL.

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
- Offline regression/integration tests.
- `pip-audit`.
- SearXNG YAML validation và production Docker build trên Python 3.12 job.

Coverage quan trọng:

- `tests/test_search_backend_auto.py` — unified search policy/fallback và việc loại Tavily.
- `tests/test_image_search.py` — image normalization/fallback/tool payload/Telegram delivery.
- `tests/test_free_routing.py` — free-tier-first defaults, provider order và metadata isolation.
- `tests/test_provider_metadata.py` — OpenRouter reasoning metadata replay.
- `tests/test_x_reader.py` — X URL parser, FxTwitter status/thread, oEmbed fallback, limits.
- `tests/test_url_service.py` — unified URL dispatch, feature flag, generic fallback.
- `tests/test_url_tool_integration.py` — `fetch_url` mode, canonical source, duplicate-call cache.
- `tests/test_search_policy_prompt.py` — direct URL fetch-first policy và chống mirror-search loop.

Tests dùng fake provider/Telegram transports, mock HTTP và local fixtures; CI **không** chứng minh live
FxTwitter/X/provider E2E trên VPS production.

---

## 13. Cấu trúc repo

```text
app/
├── main.py
├── config.py
├── ai/
│   ├── base.py
│   ├── capabilities.py
│   ├── health.py
│   ├── router.py
│   ├── multimodal.py
│   └── gemini.py / groq.py / openrouter.py / cloudflare.py
├── bot/
│   ├── filters.py
│   ├── handlers.py
│   ├── image_results.py
│   └── media.py
├── core/
│   ├── orchestrator.py
│   ├── telegram_formatting.py
│   └── request/context/rate_limiter/stats/formatting
└── search/
    ├── service.py / image_service.py
    ├── searxng_backend.py / ddgs_backend.py
    ├── reader.py              # generic SSRF-safe reader
    ├── url_service.py         # unified URL dispatcher
    └── x_reader.py            # FxTwitter/oEmbed structured X reader

tests/
├── run_tests.py
├── test_search_backend_auto.py
├── test_image_search.py
├── test_free_routing.py
├── test_provider_metadata.py
├── test_x_reader.py
├── test_url_service.py
├── test_url_tool_integration.py
└── test_search_policy_prompt.py

docs/
├── TELEGRAM_FORMATTING.md
├── X_CONTENT_FETCHING.md
├── SEARXNG_DDG_INCIDENT_2026-09-08.md
├── telegram-vision-input.md
└── superpowers/plans/        # historical plan snapshots

.github/workflows/audit.yml
Dockerfile
docker-compose.yml
.env.example
searxng/settings.example.yml
searxng/limiter.toml
DEPLOY_SEARXNG_VPS.md
```
