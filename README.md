# 🤖 fucking-cool-ai-bot

Telegram AI bot cho **group/supergroup được allowlist**, chạy bằng Docker Compose trên VPS.
Bot hỗ trợ text, image understanding, web/image search, direct URL reading, X/Twitter status/thread,
capability-aware AI fallback và Telegram-native HTML formatting.

## Tài liệu đang duy trì

- [B.AI integration](docs/BAI_INTEGRATION.md) — model allowlist, routing, probe và rollout.
- [Telegram vision input](docs/telegram-vision-input.md) — input ảnh, capability routing và memory safety.
- [Telegram-native formatting](docs/TELEGRAM_FORMATTING.md) — sanitizer, splitter và fallback plain text.
- [Search resilience](docs/SEARCH_RESILIENCE.md) — timeout budget, circuit breaker, cache, singleflight và fallback.
- [Crawl4AI URL reading](docs/CRAWL4AI_INTEGRATION.md) — private rendering backend, security, rollout và rollback.
- [X/Twitter content fetching](docs/X_CONTENT_FETCHING.md) — FxTwitter/oEmbed specialization, safety và rollback.
- [Đồng bộ `.env`](docs/ENV_SYNC.md) — giữ secret/value hiện tại khi sync theo `.env.example`.
- [SearXNG production trên VPS](DEPLOY_SEARXNG_VPS.md) — profile SearXNG private trong Docker.
- [SearXNG DuckDuckGo incident 2026-09-08](docs/SEARXNG_DDG_INCIDENT_2026-09-08.md) — incident note và workaround.

> `README.md`, `.env.example`, source code, Docker/CI và các guide runtime ở trên là source of truth.
> `docs/superpowers/plans/` là snapshot kế hoạch lịch sử, không phải tài liệu vận hành cần đồng bộ sau mỗi PR.

---

## 1. Khả năng hiện tại

- Chỉ hoạt động trong `group`/`supergroup` thuộc `ALLOWED_GROUP_IDS`.
- Có `LEARN_GROUP_ID_MODE` để bootstrap `chat_id`; ngoài learn-mode bot tự rời chat lạ khi được add.
- Trigger bằng `/ask`, `@mention`, hoặc reply trực tiếp vào tin của bot.
- Context hội thoại ngắn hạn giữ trong RAM. Chat thường dùng `chat_id`; Telegram forum topic được cô lập theo `(chat_id, message_thread_id)` để topic khác không dùng chung history/lock.
- Text route theo `TEXT_PROVIDER_ORDER`; default: **B.AI → Gemini → Groq → Cloudflare → OpenRouter**.
- Vision route theo `VISION_PROVIDER_ORDER`; default: **B.AI → Gemini → Groq Qwen 3.8 → Cloudflare → Groq Qwen 3.6**.
- B.AI chỉ được tạo khi có key/model hợp lệ và slot `bai` còn nằm trong order; để trống key thì tự skip.
- B.AI vision hiện cố ý giới hạn `max_images=1`; các provider vision khác có thể dùng ceiling `MAX_IMAGES_PER_REQUEST`.
- Telegram loader lấy ảnh từ message hiện tại và/hoặc message được reply, nên flow hiện tại tối đa 2 ảnh thực tế/request.
- Model có ba tool: `web_search`, `image_search`, `fetch_url`.
- `fetch_url` nhận `mode=auto|x_thread`; URL luôn qua SSRF validation, X status ưu tiên FxTwitter v2 → X oEmbed; sau specialized layer, Crawl4AI là URL renderer ưu tiên trước generic reader khi được cấu hình.
- Web/image search dùng `SEARCH_BACKEND=auto|searxng|ddgs`; `auto` ưu tiên SearXNG rồi fallback DDGS với timeout budget, circuit breaker, cache và singleflight.
- Crawl4AI **không** tham gia search discovery/ranking/cache/circuit breaker/singleflight; production chỉ dùng `POST /crawl`, không dùng LLM filter path.
- Image search trả ảnh qua Telegram; full image URL lỗi sẽ thử thumbnail URL.
- Answer chính được sanitize/split thành Telegram HTML an toàn; message không có markup được gửi plain text, chỉ dùng `parse_mode="HTML"` khi cần.
- `/status` cho admin hiển thị uptime, provider distribution, fallback, search, lỗi gần nhất và provider cooldown.
- Fallback count là state request-local bằng `ContextVar`, nên request đồng thời không ghi đè metric của nhau.
- CI kiểm tra Python 3.11/3.12, targeted Crawl4AI tests, full regression suite, Ruff, `pip check`, compile, Docker Compose config, dependency audit; job 3.12 còn validate SearXNG YAML và build production Docker image.

---

## 2. Kiến trúc runtime

```text
Telegram group / forum topic
   │
   ├─ allowlist / trigger / per-user rate-limit
   ├─ per-conversation lock
   ├─ TelegramMediaLoader ──► UserRequest(text, quoted_text, ephemeral images)
   ▼
Orchestrator
   │
   ├─ text request  ──► text-capable provider pool
   ├─ image request ──► vision-capable provider pool
   │
   └─ tools
       ├─ web_search ─────► SEARCH_BACKEND = auto | searxng | ddgs
       │                     auto: fresh cache → SearXNG → DDGS → stale cache
       ├─ image_search ───► cùng policy, threshold riêng cho image
       └─ fetch_url
            ├─ validate_public_url() trước mọi reader
            ├─ X/Twitter status + X_FETCH_ENABLED=1
            │    └─ FxTwitter v2 → X oEmbed
            └─ canonical/ordinary URL
                 └─ Crawl4AI POST /crawl → generic SSRF-safe reader
```

Provider router lọc capability trước khi fallback. Tool budget dùng chung qua retry/fallback.
Với URL cụ thể do user cung cấp và yêu cầu đọc nội dung, system policy ưu tiên `fetch_url` trước `web_search`.

---

## 3. Yêu cầu trước khi chạy

- Python runtime trong Docker: **3.12**.
- Docker + Docker Compose plugin trên VPS.
- Telegram bot token từ BotFather.
- Tối thiểu một text provider khả dụng: B.AI, Gemini, Groq, Cloudflare Workers AI hoặc OpenRouter.
- Privacy Mode nên tắt nếu muốn bot đọc message/reply trong group theo workflow hiện tại.

Default hiện tại tối ưu theo hướng **free-tier-first**, nhưng code không thể cưỡng chế billing. B.AI zero-credit là promotion có thể thay đổi; các provider khác cũng có quota/pricing riêng. Theo dõi dashboard/billing của từng account production.

---

## 4. Cài đặt nhanh

```bash
git clone https://github.com/vanphandinh/fucking-cool-ai-bot.git
cd fucking-cool-ai-bot

cp .env.example .env
nano .env

# tối thiểu:
# BOT_TOKEN=...
# BAI_API_KEY=...       # hoặc Gemini / Groq / Cloudflare / OpenRouter
# ADMIN_IDS=...
# ALLOWED_GROUP_IDS=... # hoặc dùng learn-mode ở mục 5
docker compose up -d --build
docker compose logs -f bot
```

Nếu cấu hình sai `SEARCH_BACKEND`, `LOG_LEVEL`, timeout hoặc giá trị số có constraint, `Settings` fail-fast khi startup. Riêng Crawl4AI thiếu URL/token sẽ degrade về generic reader thay vì làm bot fail startup.

### Nâng cấp deployment đã có `.env`

Không copy đè `.env` bằng `.env.example`. Chạy script sync để giữ value/secret hiện tại, thêm key mới, bỏ key đã xóa và đồng bộ comment/order:

```bash
git pull
python scripts/sync_env.py
```

Chi tiết: [docs/ENV_SYNC.md](docs/ENV_SYNC.md).

Các default quan trọng hiện tại:

```env
BAI_TEXT_MODEL=qwen3.8-flash
BAI_VISION_MODEL=qwen3.8-flash
BAI_REQUEST_TIMEOUT_SEC=30.0
GEMINI_MODEL=gemini-3.8-flash
GROQ_MODEL=openai/gpt-oss-120b
OPENROUTER_MODEL=openrouter/free
CLOUDFLARE_TEXT_MODEL=@cf/zai-org/glm-4.7-flash
TEXT_PROVIDER_ORDER=bai,gemini,groq,cloudflare,openrouter
VISION_PROVIDER_ORDER=bai,gemini,groq_qwen38,cloudflare,groq_qwen36
SEARCH_BACKEND=auto
SEARXNG_TIMEOUT_SEC=7.0
DDGS_TIMEOUT_SEC=8.0
SEARCH_TOTAL_TIMEOUT_SEC=15.0
SEARCH_CIRCUIT_FAILURE_THRESHOLD=3
SEARCH_CIRCUIT_COOLDOWN_SEC=30.0
SEARCH_RATE_LIMIT_COOLDOWN_SEC=180.0
SEARCH_CACHE_MAX_ENTRIES=256
SEARCH_WEB_CACHE_TTL_SEC=60.0
SEARCH_IMAGE_CACHE_TTL_SEC=120.0
SEARCH_STALE_CACHE_TTL_SEC=900.0
IMAGE_SEARCH_MAX_RESULTS=4
X_FETCH_ENABLED=1
CRAWL4AI_ENABLED=1
CRAWL4AI_URL=http://crawl4ai:11235
CRAWL4AI_API_TOKEN=
CRAWL4AI_TIMEOUT_SEC=25.0
CRAWL4AI_MAX_CHARS=12000
CRAWL4AI_IMAGE=unclecode/crawl4ai:0.9.3
CRAWL4AI_SHM_SIZE=512m
MAX_CONTEXT_TURNS=6
MAX_TOOL_ROUNDS=2
```

Nếu `.env` cũ còn `TAVILY_API_KEY`, `IMAGE_SEARCH_BACKEND` hoặc biến NVIDIA/NIM cũ, `scripts/sync_env.py` sẽ loại chúng vì `.env.example` không còn các key đó.

---

## 5. Lấy `chat_id` và allowlist

```env
LEARN_GROUP_ID_MODE=1
ALLOWED_GROUP_IDS=
```

Khởi động bot và xem log:

```bash
docker compose up -d --build
docker compose logs -f bot
# GROUP_ID_LEARN: chat_id=-1001234567890 ...
```

Sau đó khóa lại:

```env
LEARN_GROUP_ID_MODE=0
ALLOWED_GROUP_IDS=-1001234567890
```

Có thể cấu hình nhiều group bằng CSV.

---

## 6. Cách dùng

| Flow | Ví dụ / hành vi |
|---|---|
| `/ask` | `/ask giải thích blockchain ngắn gọn` |
| Mention | `@FuckingCoolAIbot giá vàng hôm nay?` |
| Tìm ảnh | `@FuckingCoolAIbot tìm cho tôi 4 ảnh capybara` |
| Đọc URL | Gửi URL + yêu cầu đọc/tóm tắt; AI ưu tiên `fetch_url` trước search |
| Đọc X post | Gửi `https://x.com/<user>/status/<id>` + câu hỏi |
| Đọc X thread | Yêu cầu đọc cả thread; AI có thể gọi `fetch_url(..., mode="x_thread")` |
| Reply bot | Reply tin của bot rồi nhập câu hỏi; không bắt buộc mention lại |
| Reply thành viên | Cần `@mention` hoặc `/ask` |
| Ảnh hiện tại | Telegram photo hoặc JPEG/PNG/WebP document + caption trigger |
| Ảnh được reply | Reply ảnh rồi dùng `@mention` hoặc `/ask <câu hỏi>` |
| `/help` | Hướng dẫn ngắn |
| `/status` | Chỉ admin trong `ADMIN_IDS` |

Plain image không có text/caption trigger sẽ không tự gọi bot.

---

## 7. Cấu hình provider

### Text pool

| Biến | Default | Vai trò |
|---|---|---|
| `BAI_API_KEY` / `BAI_TEXT_MODEL` | `qwen3.8-flash` | Default slot đầu khi có key; promotion zero-credit cần theo dõi |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | `gemini-3.8-flash` | Default slot thứ hai |
| `GROQ_API_KEY` / `GROQ_MODEL` | `openai/gpt-oss-120b` | Fallback text |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_TEXT_MODEL` | `@cf/zai-org/glm-4.7-flash` | Fallback text |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | `openrouter/free` | Free-model router fallback |
| `TEXT_PROVIDER_ORDER` | `bai,gemini,groq,cloudflare,openrouter` | Thứ tự hiệu lực |

Startup yêu cầu ít nhất một provider vừa có credential/model hợp lệ vừa nằm trong order.

### Vision pool

| Slot | Default model | Giới hạn ảnh |
|---|---|---:|
| `bai` | `qwen3.8-flash` | **1** |
| `gemini` | `gemini-3.8-flash` | `MAX_IMAGES_PER_REQUEST` |
| `groq_qwen38` | model 1 trong `GROQ_VISION_MODELS` | 3 |
| `cloudflare` | `@cf/google/gemma-4-26b-a4b-it` | `MAX_IMAGES_PER_REQUEST` |
| `groq_qwen36` | model 2 trong `GROQ_VISION_MODELS` | 3 |

```env
VISION_ENABLED=1
VISION_PROVIDER_ORDER=bai,gemini,groq_qwen38,cloudflare,groq_qwen36
MAX_IMAGES_PER_REQUEST=3
MAX_IMAGE_BYTES=8388608
MAX_TOTAL_IMAGE_BYTES=12582912
```

Request có nhiều hơn capability của slot sẽ skip slot đó thay vì gửi payload không tương thích. Ví dụ request 2 ảnh sẽ bỏ qua B.AI vision và thử provider vision kế tiếp.

Chi tiết B.AI: [docs/BAI_INTEGRATION.md](docs/BAI_INTEGRATION.md). Chi tiết Telegram vision: [docs/telegram-vision-input.md](docs/telegram-vision-input.md).

---

## 8. Provider fallback và health

Default text:

```text
B.AI / qwen3.8-flash
  → Gemini / gemini-3.8-flash
  → Groq / openai/gpt-oss-120b
  → Cloudflare / @cf/zai-org/glm-4.7-flash
  → OpenRouter / openrouter/free
```

Default vision:

```text
B.AI / qwen3.8-flash
  → Gemini / gemini-3.8-flash
  → Groq Qwen 3.8
  → Cloudflare Gemma 4
  → Groq Qwen 3.6
```

Health state trong RAM:

- `401/403`: disable slot đến process restart.
- `429`: cooldown theo numeric `Retry-After`, nếu không parse được thì mặc định 60 giây.
- Network/`5xx` transient: sau 2 lỗi liên tiếp cooldown 30 giây.
- Success reset transient state.

Tool budget dùng chung qua fallback: `MAX_TOOL_ROUNDS=2`, safety cap nội bộ tối đa 8 tool calls/completion.
Provider-specific metadata được giữ trong cùng provider rồi strip trước cross-provider fallback.

`last_fallbacks` là `ContextVar` request-local; `/status`/stats không bị sai chỉ vì hai request async fallback đồng thời.

---

## 9. Web/image search resilience

`SEARCH_BACKEND=auto` dùng policy:

```text
fresh cache
  → SearXNG (tối đa 1 attempt/request)
  → DDGS (tối đa 1 attempt/request)
  → stale cache nếu upstream unavailable
  → SearchError nếu không còn dữ liệu
```

Web và image dùng circuit riêng. Web SearXNG có 2+ result thì return; 1 result sẽ giữ lại và top-up từ DDGS. Image chỉ cần 1 usable SearXNG result nên không top-up.

Các request đồng thời có cùng `(kind, limit, normalized_query)` dùng singleflight để chia sẻ một pipeline upstream. Khi waiter cuối cùng bị cancel, upstream task còn chạy sẽ bị cancel; shared SearXNG HTTP client được đóng khi app shutdown.

Crawl4AI không nằm trong pipeline này và không thay đổi search cache/circuit/singleflight/source policy.

Chi tiết: [docs/SEARCH_RESILIENCE.md](docs/SEARCH_RESILIENCE.md).

---

## 10. Direct URL / X content / Crawl4AI

`fetch_url(url, mode="auto")` đọc URL thường hoặc focal X status. `mode="x_thread"` chỉ hợp lệ cho X/Twitter status URL.

Supported specialized hosts: `x.com`, `twitter.com`, mobile variants, `fxtwitter.com`, `fixupx.com`. Host giả như `x.com.evil.example` không được specialized.

```text
mọi URL:
  validate_public_url()

X mode=auto:
  FxTwitter /2/status/{id}
    → X publish.oEmbed
    → canonical x.com URL
    → Crawl4AI /crawl
    → generic reader

X mode=x_thread:
  FxTwitter /2/thread/{id}
    → FxTwitter focal /2/status/{id}
    → X publish.oEmbed
    → canonical x.com URL
    → Crawl4AI /crawl
    → generic reader

URL thường / X specialization bị tắt:
  Crawl4AI /crawl
    → generic reader
```

Crawl4AI chỉ được gọi khi `CRAWL4AI_ENABLED=1`, URL service và bearer token đều được cấu hình. Nó nhận fixed payload do server tạo, không nhận provider keys hoặc arbitrary browser/hooks/JS config. `CancelledError` propagate; timeout/network/401/403/429/5xx/oversized/malformed/failed/empty response chỉ fallback generic **một lần**. Response Crawl4AI được stream và cap 4 MiB trước JSON parse; text trả về được cap theo `CRAWL4AI_MAX_CHARS`.

Production **không dùng** `/md?f=fit`, `/llm` hoặc `/ask`; integration dùng `POST /crawl` để tránh double-LLM path.

Chi tiết: [docs/CRAWL4AI_INTEGRATION.md](docs/CRAWL4AI_INTEGRATION.md) và [docs/X_CONTENT_FETCHING.md](docs/X_CONTENT_FETCHING.md).

---

## 11. Telegram formatting và forum isolation

- `split_telegram_html()` sanitize legacy Markdown/HTML và chia message theo tối đa 3900 UTF-16 code units.
- Chỉ payload có markup thực sự mới gửi `parse_mode="HTML"`; plain payload được gửi plain text.
- Nếu Telegram báo parse/entity error cho HTML, chunk đó được retry plain text. Lỗi 400 khác không retry mù.
- ChatMemory lưu assistant response dạng plain text.
- Forum topic có history và per-conversation lock riêng; topic chậm không chặn topic khác trong cùng supergroup.

Chi tiết: [docs/TELEGRAM_FORMATTING.md](docs/TELEGRAM_FORMATTING.md).

---

## 12. SearXNG và Crawl4AI private profiles

Profile `searxng` không publish port ra host. Bot gọi nội bộ `http://searxng:8080`.

```bash
cp -i searxng/settings.example.yml searxng/settings.yml
openssl rand -hex 32
nano searxng/settings.yml

docker compose --profile searxng up -d --build
```

Crawl4AI cũng không publish port `11235`. Tạo bearer token riêng trong `.env`, rồi bật profile:

```bash
openssl rand -hex 32
# đặt kết quả vào CRAWL4AI_API_TOKEN trong .env

docker compose --profile searxng --profile crawl4ai up -d --build
docker compose exec bot python scripts/smoke_crawl4ai.py
```

Rollback URL renderer ngay bằng `CRAWL4AI_ENABLED=0`; search vẫn giữ SearXNG → DDGS như cũ.

Guide đầy đủ: [DEPLOY_SEARXNG_VPS.md](DEPLOY_SEARXNG_VPS.md) và [docs/CRAWL4AI_INTEGRATION.md](docs/CRAWL4AI_INTEGRATION.md).

---

## 13. Security và dữ liệu

- `.env` và `searxng/settings.yml` chứa secret và nằm trong `.gitignore`.
- Docker image chạy non-root (`appuser`).
- Không có database; context, stats, rate-limit, provider health và search runtime đều in-memory.
- Raw image bytes không được ghi vào ChatMemory.
- Generic web reader có SSRF guard cho URL/DNS/redirect.
- Bot chạy cùng public-URL validation trước khi gửi URL sang Crawl4AI; redirected/canonical URL trả về chỉ được dùng làm source sau khi validate lại.
- Crawl4AI chỉ ở Docker network private, không publish `11235`, dùng bearer token; JS execution và hooks bị tắt.
- Crawl4AI không được nhận Gemini/OpenAI/Groq/B.AI/Cloudflare/OpenRouter credentials; client dùng `trust_env=False` và response cap 4 MiB.
- Specialized X API egress dùng host cố định trong code; source user-facing vẫn canonical `x.com`.
- Search-result image URL được Telegram fetch trực tiếp; bot server không tải arbitrary image result về RAM.
- Shutdown cancel/join handler tasks, đóng Telegram session, Crawl4AI client, shared search client và provider clients.

---

## 14. Vận hành

```bash
docker compose ps
docker compose logs --tail=100 bot
docker compose up -d --build
```

Kiểm tra effective config:

```bash
docker compose exec -T bot python - <<'PY'
from app.config import Settings
s = Settings()
print("TEXT_PROVIDER_ORDER   =", s.text_provider_order)
print("VISION_PROVIDER_ORDER =", s.vision_provider_order)
print("SEARCH_BACKEND        =", s.search_backend)
print("SEARCH_TOTAL_TIMEOUT  =", s.search_total_timeout_sec)
print("X_FETCH_ENABLED       =", s.x_fetch_enabled)
print("CRAWL4AI_ENABLED      =", s.crawl4ai_enabled)
print("CRAWL4AI_URL          =", s.crawl4ai_url)
print("CRAWL4AI_TIMEOUT_SEC  =", s.crawl4ai_timeout_sec)
print("MAX_CONTEXT_TURNS     =", s.max_context_turns)
print("MAX_TOOL_ROUNDS       =", s.max_tool_rounds)
PY
```

Nếu dùng SearXNG:

```bash
docker compose --profile searxng ps
docker compose logs --tail=100 searxng
docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/healthz
```

Nếu dùng Crawl4AI:

```bash
docker compose --profile crawl4ai ps
docker compose logs --tail=100 crawl4ai
docker compose exec bot python scripts/smoke_crawl4ai.py
```

---

## 15. Tests và CI

```bash
python -m pip install -r requirements.txt
python -m pip install ruff==0.16.6 pip-audit==2.10.1

python -m unittest discover -s tests -p 'test_crawl4ai*.py' -v
python -m unittest discover -s tests -p 'test_url_service.py' -v
python -m unittest discover -s tests -p 'test_url_tool_integration.py' -v
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m compileall -q app tests scripts
python -m pip check
python -m pip_audit --progress-spinner off
cp .env.example .env && docker compose config --quiet
```

Workflow [Audit checks](.github/workflows/audit.yml) chạy trên push, pull request và `workflow_dispatch`:

- Python 3.11 + 3.12.
- Targeted Crawl4AI/URL tests trước full suite.
- Ruff, `pip check`, `compileall`.
- Offline regression/integration tests.
- `pip-audit`.
- Docker Compose config validation.
- SearXNG YAML validation và production Docker build ở Python 3.12.

Coverage quan trọng:

- `tests/test_free_routing.py` — B.AI-first defaults, capability order, provider metadata isolation.
- `tests/test_bai_provider.py`, `tests/test_bai_probe.py` — B.AI contract/probe behavior.
- `tests/test_router_concurrency.py` — request-local fallback metric under concurrency.
- `tests/test_forum_topic_isolation.py` — forum topic history/lock isolation.
- `tests/test_search_backend_auto.py`, `tests/test_search_cancellation.py` — resilient search routing, cancellation và client lifecycle.
- `tests/test_image_search.py` — image normalization/delivery.
- `tests/test_crawl4ai_client.py`, `tests/test_crawl4ai_compose.py` — auth, bounded response, parsing, cancellation và Docker invariants.
- `tests/test_x_reader.py`, `tests/test_url_service.py`, `tests/test_url_tool_integration.py` — X/direct URL routing, SSRF, fallback và tool semantics.
- `tests/test_search_policy_prompt.py` — fetch-first policy và chống mirror-search loop.

CI không chứng minh live provider/X/SearXNG/Crawl4AI E2E trên VPS production; live Crawl4AI smoke và live provider probes là bước manual riêng.

---

## 16. Cấu trúc repo

```text
app/
├── main.py
├── config.py
├── ai/
│   ├── bai.py / gemini.py / groq.py / cloudflare.py / openrouter.py
│   ├── base.py / capabilities.py / health.py / router.py / multimodal.py
├── bot/
│   ├── filters.py / handlers.py / image_results.py / media.py
├── core/
│   ├── orchestrator.py / request.py / context.py / rate_limiter.py / stats.py
│   ├── telegram_formatting.py / formatting.py / source_policy.py
└── search/
    ├── service.py / image_service.py / router.py / resilience.py / runtime.py
    ├── searxng_backend.py / ddgs_backend.py
    ├── reader.py / url_service.py / x_reader.py / crawl4ai_client.py

scripts/
├── sync_env.py
├── probe_bai.py
└── smoke_crawl4ai.py

docs/
├── BAI_INTEGRATION.md
├── CRAWL4AI_INTEGRATION.md
├── ENV_SYNC.md
├── SEARCH_RESILIENCE.md
├── TELEGRAM_FORMATTING.md
├── X_CONTENT_FETCHING.md
├── SEARXNG_DDG_INCIDENT_2026-09-08.md
├── telegram-vision-input.md
└── superpowers/plans/        # historical snapshots

.github/workflows/audit.yml
Dockerfile
docker-compose.yml
.env.example
searxng/settings.example.yml
searxng/limiter.toml
DEPLOY_SEARXNG_VPS.md
```
