# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.
Bot hỗ trợ cả **text + image understanding**, có tool tìm/đọc web, fallback AI theo capability,
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
- Text route: Gemini → Groq → OpenRouter, chỉ dùng provider có key.
- Vision route tách riêng, chọn theo `VISION_PROVIDER_ORDER`: Gemini vision, tối đa 2 Groq vision slot,
  và Cloudflare Workers AI reserve.
- Nhận Telegram photo hoặc JPEG/PNG/WebP gửi dạng document.
- Ở flow Telegram hiện tại, một request có thể lấy ảnh từ **message hiện tại + message được reply**
  (tối đa 2 ảnh thực tế). `MAX_IMAGES_PER_REQUEST` là trần capability/config, không tự thêm album support.
- Model có thể gọi `web_search` và `fetch_url`; search backend được chọn bằng `SEARCH_BACKEND`.
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
   └─ tools ──► web_search / fetch_url
                  │
                  └─ SEARCH_BACKEND = ddgs | searxng | tavily
```

Provider router lọc theo capability trước khi fallback. Text request không rơi sang vision slot và image
request không rơi sang text-only slot.

---

## 3. Yêu cầu trước khi chạy

- Python runtime trong Docker: **3.12**.
- Docker + Docker Compose plugin trên VPS.
- Telegram bot token từ BotFather.
- Tối thiểu một text provider key trong `GEMINI_API_KEY`, `GROQ_API_KEY` hoặc `OPENROUTER_API_KEY`.
- Privacy Mode nên tắt nếu muốn bot đọc message/reply trong group theo workflow hiện tại.

Provider/model/rate-limit của dịch vụ bên thứ ba có thể thay đổi theo thời gian; repo chỉ đảm bảo các default
đang được cấu hình trong source và `.env.example`.

---

## 4. Cài đặt nhanh

```bash
git clone https://github.com/vanphandinh/fucking-cool-ai-bot.git
cd fucking-cool-ai-bot

cp .env.example .env
nano .env

# tối thiểu điền:
# BOT_TOKEN=...
# GEMINI_API_KEY=...   # hoặc GROQ_API_KEY / OPENROUTER_API_KEY
# ADMIN_IDS=...
# ALLOWED_GROUP_IDS=...  # hoặc dùng learn-mode ở mục 5

docker compose up -d --build
docker compose logs -f bot
```

Nếu cấu hình sai `SEARCH_BACKEND`, `LOG_LEVEL`, timeout hoặc các giá trị số có constraint, app sẽ fail-fast
khi load `Settings` thay vì chạy với cấu hình mơ hồ.

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
| `GEMINI_API_KEY` / `GEMINI_MODEL` | `gemini-2.5-flash` | Text provider ưu tiên 1 |
| `GROQ_API_KEY` / `GROQ_MODEL` | `llama-3.3-70b-versatile` | Text fallback |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | `meta-llama/llama-3.3-70b-instruct:free` | Text fallback cuối |

Startup yêu cầu **ít nhất một** text provider key.

### 7.3 Vision provider pool

| Biến | Default | Ý nghĩa |
|---|---|---|
| `VISION_ENABLED` | `1` | Bật/tắt toàn bộ vision slot; text route không bị ảnh hưởng |
| `GEMINI_VISION_MODEL` | `gemini-3.8-flash` | Model của Gemini vision slot |
| `GROQ_VISION_MODELS` | `qwen/qwen3.8-27b,qwen/qwen3.6-27b` | Tối đa hai Groq vision slot theo thứ tự list |
| `CLOUDFLARE_ACCOUNT_ID` | trống | Bắt buộc cùng API token để tạo Cloudflare vision slot |
| `CLOUDFLARE_API_TOKEN` | trống | Token Workers AI |
| `CLOUDFLARE_VISION_MODEL` | `@cf/google/gemma-4-26b-a4b-it` | Cloudflare vision model |
| `VISION_PROVIDER_ORDER` | `gemini,groq_qwen38,groq_qwen36,cloudflare` | Thứ tự fallback vision hiệu lực |
| `MAX_IMAGES_PER_REQUEST` | `3` | Capability/config ceiling; Telegram loader hiện chỉ cung cấp current + reply |
| `MAX_IMAGE_BYTES` | `8388608` | Trần bytes cho từng ảnh |
| `MAX_TOTAL_IMAGE_BYTES` | `12582912` | Trần tổng bytes ảnh trong một request |

`configured_vision_provider_names` chỉ báo các slot vừa có credential/model phù hợp vừa nằm trong
`VISION_PROVIDER_ORDER`.

### 7.4 Web search

| Biến | Default | Ý nghĩa |
|---|---|---|
| `SEARCH_BACKEND` | `ddgs` | Chọn đúng một backend: `ddgs`, `searxng`, `tavily` |
| `SEARXNG_URL` | `http://searxng:8080` trong `.env.example` | URL JSON API khi chọn SearXNG |
| `TAVILY_API_KEY` | trống | Bắt buộc khi chọn Tavily |

Search layer **không tự fallback** giữa DDGS/SearXNG/Tavily. Nếu backend đã chọn lỗi, tool trả lỗi về model;
đó là luồng khác với fallback AI provider.

Các biến Compose-only cho SearXNG:

- `SEARXNG_IMAGE`
- `GRANIAN_BLOCKING_THREADS` (default `1`)
- `GRANIAN_BACKPRESSURE` (default `2`)

### 7.5 Limits / runtime

| Biến | Default | Ý nghĩa |
|---|---|---|
| `MAX_QUESTIONS_PER_MIN_PER_USER` | `3` | Rate-limit RAM theo user; `0` = chặn toàn bộ câu hỏi |
| `MAX_CONTEXT_TURNS` | `10` | Số cặp hỏi/đáp gần nhất giữ theo chat |
| `MAX_TOOL_ROUNDS` | `4` | Số vòng tool tối đa trong completion |
| `REQUEST_TIMEOUT_SEC` | `60.0` | Timeout HTTP/provider/reader |
| `QUESTION_TIMEOUT_SEC` | `180.0` | Deadline tổng của một câu hỏi, gồm chờ chat lock |
| `LOG_LEVEL` | `INFO` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, `DEBUG` (`WARN` được normalize) |

Router còn có safety cap nội bộ tối đa 8 tool calls cho một completion, dùng chung qua retry/fallback.

---

## 8. Routing, fallback và provider health

### Text

Thứ tự được tạo cố định theo key đã cấu hình:

```text
Gemini -> Groq -> OpenRouter
```

### Vision

Vision slot được tạo từ credential hiện có rồi xếp theo `VISION_PROVIDER_ORDER`.
Cloudflare hiện chỉ là **vision provider**, không nằm trong text pool.

Provider health là state trong RAM:

- `401/403`: disable slot đến khi process restart.
- `429`: cooldown theo numeric `Retry-After`; nếu không parse được thì mặc định 60 giây.
- Network/`5xx` transient: sau 2 lỗi liên tiếp thì cooldown 30 giây.
- Thành công reset transient counter/cooldown.

Fallback giữ chung tool budget của request; provider không hỗ trợ tools có thể retry plain mode theo error
classification thay vì làm hỏng toàn bộ route.

---

## 9. Web search và đọc trang

Orchestrator expose hai tool:

- `web_search(query)` — gọi backend đang chọn.
- `fetch_url(url)` — đọc một URL cụ thể.

`fetch_url` chỉ chấp nhận destination public; reader kiểm tra URL/DNS/redirect để chặn localhost, private
network và các address class không được hỗ trợ. Reader cũng giới hạn body/deadline và từ chối compressed
response trong đường đọc trực tiếp để tránh memory amplification.

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
- không có text provider key → dừng.
- vision bật nhưng không có vision provider hợp lệ → warning; text bot vẫn chạy.
- `SEARCH_BACKEND=searxng` nhưng URL trống hoặc `tavily` nhưng thiếu key → warning lúc startup; tool sẽ lỗi khi dùng.

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

Tests dùng fake Telegram/provider transports và local fixtures; CI không chứng minh live credential/provider
E2E trong môi trường production.

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
│   └── media.py           # Telegram image validation/download bounds
├── core/                  # request, orchestrator, memory, rate-limit, stats, formatting
└── search/                # ddgs/searxng/tavily + safe page reader

tests/
├── run_tests.py
├── test_audit_regressions.py
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
