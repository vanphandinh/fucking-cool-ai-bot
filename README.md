# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.

AI runtime hiện là **B.AI-only**: không còn Gemini, Groq, OpenRouter hay Cloudflare Workers AI trong source/runtime. Bot vẫn giữ web search, image search, direct URL reading, X/Twitter reader, Crawl4AI, vision input và Telegram-native HTML formatting.

## Tài liệu đang duy trì

- [B.AI integration](docs/BAI_INTEGRATION.md) — model allowlist, tool calling, health, synthesis và probe.
- [Telegram vision input](docs/telegram-vision-input.md) — input ảnh, giới hạn 1 ảnh/request và memory safety.
- [Telegram-native formatting](docs/TELEGRAM_FORMATTING.md).
- [Search resilience](docs/SEARCH_RESILIENCE.md).
- [Crawl4AI URL reading](docs/CRAWL4AI_INTEGRATION.md).
- [X/Twitter content fetching](docs/X_CONTENT_FETCHING.md).
- [Đồng bộ `.env`](docs/ENV_SYNC.md).
- [SearXNG production trên VPS](DEPLOY_SEARXNG_VPS.md).

> `README.md`, `.env.example`, source code, Docker/CI và các guide runtime ở trên là source of truth. `docs/superpowers/` là snapshot thiết kế/kế hoạch lịch sử nên có thể chứa tên provider đã bị loại khỏi runtime.

---

## 1. Kiến trúc hiện tại

```text
Telegram group / forum topic
   │
   ├─ allowlist / trigger / rate-limit / per-conversation lock
   ├─ TelegramMediaLoader ──► UserRequest(text, quoted_text, optional image)
   ▼
Orchestrator
   │
   ├─ text  ───────────────► B.AI text slot
   ├─ image ───────────────► B.AI vision slot (max 1 image)
   │
   └─ tools
       ├─ web_search ─────► SearXNG / DDGS
       ├─ image_search ───► SearXNG / DDGS
       └─ fetch_url
            ├─ X/Twitter resolver khi phù hợp
            ├─ Crawl4AI khi được cấu hình
            └─ generic SSRF-safe reader
```

B.AI dùng endpoint OpenAI-compatible:

```text
POST https://api.b.ai/v1/chat/completions
```

Không có cross-provider fallback. Nếu B.AI lỗi/cooldown/unavailable, request kết thúc với thông báo B.AI-specific thay vì chuyển sang AI provider khác.

---

## 2. B.AI model policy

Default production:

```env
BAI_TEXT_MODEL=qwen3.8-flash
BAI_VISION_MODEL=qwen3.8-flash
```

Integration cho phép chọn thủ công các model promotion đã được validate khi support được thêm:

- `qwen3.8-flash`
- `mimo-v2.5`
- `hy3` — text-only trong integration này
- `glm-5.3-flash`

Không có automatic model rotation. Việc B.AI promotion/zero-credit có thể thay đổi upstream; repo không coi promotion là entitlement vĩnh viễn.

B.AI vision hiện được khóa `max_images=1`. Bot không tự gửi request nhiều ảnh sang text model và không fallback sang provider vision khác.

---

## 3. Tool discovery và Fresh Qwen Synthesis

Model có ba tool:

- `web_search(query)`
- `image_search(query)`
- `fetch_url(url, mode?)`

Tool budget dùng chung trong một completion:

- `MAX_TOOL_ROUNDS` — default `2`;
- hard cap nội bộ: tối đa `8` tool calls;
- tối đa `2` tool calls chạy song song.

Khi discovery budget hết, router không replay toàn bộ structured tool trajectory cho synthesis. Nó tạo **fresh synthesis context** từ original request + bounded plain-text evidence, tắt tools và gửi lại cho cùng B.AI model. Với B.AI, synthesis no-tool gửi `tool_choice=none`.

Nếu model vẫn phát tool call trong synthesis-only turn, tool đó không được thực thi và request kết thúc; không có external-provider fallback và không có fifth retry vô hạn.

---

## 4. Cài đặt nhanh

Yêu cầu:

- Docker + Docker Compose plugin;
- Telegram bot token;
- B.AI API key;
- Python 3.12 nếu chạy helper scripts trực tiếp ngoài container.

```bash
git clone https://github.com/vanphandinh/fucking-cool-ai-bot.git
cd fucking-cool-ai-bot
cp .env.example .env
nano .env
```

Cấu hình tối thiểu:

```env
BOT_TOKEN=...
BAI_API_KEY=...
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

VISION_ENABLED=1
BAI_VISION_MODEL=qwen3.8-flash
MAX_IMAGES_PER_REQUEST=1
MAX_IMAGE_BYTES=8388608
MAX_TOTAL_IMAGE_BYTES=12582912
```

`VISION_ENABLED=0` chỉ tắt image understanding; text vẫn hoạt động.

Request có hơn một ảnh sẽ bị chặn trước model call với UX rõ rằng B.AI hiện chỉ hỗ trợ một ảnh mỗi request trong bot này.

---

## 6. Nâng cấp deployment cũ

Không copy đè `.env` bằng `.env.example`. Sau `git pull`, chạy:

```bash
python scripts/sync_env.py
```

Script giữ value của key còn tồn tại và xóa key không còn trong `.env.example`.

Khi nâng cấp sang B.AI-only, script sẽ giữ `BAI_API_KEY`/B.AI settings hiện có và loại các biến AI provider cũ như Gemini/Groq/OpenRouter/Cloudflare Workers AI cùng provider-order variables. Search, Telegram và Crawl4AI settings vẫn được giữ nếu key còn tồn tại.

Chi tiết: [docs/ENV_SYNC.md](docs/ENV_SYNC.md).

---

## 7. Search / URL reading

Search không phụ thuộc AI provider list.

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

## 8. Telegram usage

| Flow | Ví dụ / hành vi |
|---|---|
| `/ask` | `/ask giải thích blockchain ngắn gọn` |
| Mention | `@FuckingCoolAIbot giá vàng hôm nay?` |
| Tìm ảnh | `@FuckingCoolAIbot tìm cho tôi ảnh capybara` |
| Đọc URL | gửi URL + yêu cầu đọc/tóm tắt |
| X/Twitter | gửi status URL + câu hỏi |
| Một ảnh | JPEG/PNG/WebP + caption trigger |
| Reply ảnh | reply ảnh rồi tag bot hoặc `/ask` |
| `/help` | hướng dẫn ngắn |
| `/status` | admin-only status |

Plain image không có text/caption trigger sẽ không tự gọi bot.

Forum topic được cô lập history/lock bằng `(chat_id, message_thread_id)`.

---

## 9. B.AI health / failure behavior

B.AI text và vision slot có health state riêng trong RAM:

- `401/403` → disable slot đến process restart;
- `429` → cooldown, ưu tiên numeric `Retry-After`;
- network/`5xx` transient → cooldown theo health policy hiện có;
- success → reset transient state.

`/status` hiển thị provider distribution, last provider, last error và cooldown/unavailable state. Cross-provider fallback counter đã bị loại vì runtime không còn provider fallback.

---

## 10. Verification

Offline/full checks:

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m pip check
python -m compileall -q app tests scripts
cp .env.example .env
docker compose config --quiet
docker build --tag fcai-bai-only-test .
```

GitHub Actions chạy Python 3.11/3.12; Python 3.12 còn validate Compose/SearXNG YAML và build production image.

Manual production smoke sau deploy nên gồm:

1. text query không tool;
2. query cần web search;
3. direct URL;
4. một ảnh;
5. thử hai ảnh để xác nhận bị chặn đúng UX;
6. `/status`;
7. B.AI failure path để xác nhận không có external AI request.
