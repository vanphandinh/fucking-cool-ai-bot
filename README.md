# 🤖 fucking-cool-ai-bot

Bot Telegram AI chạy trong **group được chỉ định**, deploy bằng **Docker Compose** trên VPS.
Trả lời bằng **AI miễn phí** (Google Gemini → Groq → OpenRouter `:free`), có **tìm kiếm web**
(DuckDuckGo / SearXNG / Tavily), chỉ nói chuyện với các group nằm trong danh sách cho phép.

> 📄 Kế hoạch triển khai chi tiết: [PLAN_TRIEN_KHAI.md](PLAN_TRIEN_KHAI.md)
> 📘 Hướng dẫn SearXNG production trên VPS: [DEPLOY_SEARXNG_VPS.md](DEPLOY_SEARXNG_VPS.md)
> ✅ Trạng thái code: **Phase P1–P2 đã viết** (MVP + web search + fallback AI).

---

## 1. Yêu cầu trước khi chạy

| Thứ | Trạng thái | Ghi chú |
|---|---|---|
| Bot Telegram `@FuckingCoolAIbot` | ✅ đã tạo | **Privacy Mode phải OFF** (`/setprivacy` → Disable) |
| VPS Ubuntu 22.04/24.04 + Docker & Compose plugin | cần làm | `docker --version`, `docker compose version` |
| Key AI miễn phí | cần làm | tối thiểu **Gemini**: https://aistudio.google.com/apikey |
| Group riêng tư | ✅ đã add bot | cần lấy **chat_id** (bước 4) |

> ⚠️ Nếu đã add bot vào group **trước** khi tắt Privacy Mode → gỡ bot rồi add lại để có hiệu lực.

---

## 2. Cài đặt nhanh trên VPS

```bash
# 1) Clone repo
git clone <url-của-repo> && cd fucking-cool-ai-bot

# 2) Tạo .env từ mẫu và điền key
cp .env.example .env
nano .env
#   - BOT_TOKEN=<token từ BotFather>
#   - GEMINI_API_KEY=<key từ aistudio.google.com>
#   - GROQ_API_KEY / OPENROUTER_API_KEY (khuyến nghị — để fallback khi Gemini hết quota)
#   - ADMIN_IDS=<id telegram của bạn, xem @userinfobot>
#   - ALLOWED_GROUP_IDS=<để trống ban đầu nếu dùng chế độ học chat_id ở bước 4>

# 3) Build & chạy bot
docker compose up -d --build
docker compose logs -f bot        # theo dõi log
```

---

## 3. Lấy chat_id của group (làm 1 lần)

Có **2 cách**:

**Cách A — chế độ học id (đơn giản nhất):**
```bash
# Trong .env: LEARN_GROUP_ID_MODE=1 (ALLOWED_GROUP_IDS để trống)
docker compose up -d --build
```
Thêm bot vào group riêng tư của bạn → xem log:
```bash
docker compose logs -f bot
# Tìm dòng: GROUP_ID_LEARN: chat_id=-1001234567890 title=...
```
Sửa `.env`: `ALLOWED_GROUP_IDS=-1001234567890`, đặt `LEARN_GROUP_ID_MODE=0`, rồi `docker compose up -d`.

**Cách B — dùng bot RawDataBot:** mở chat **@RawDataBot**, forward 1 tin bất kỳ từ group
của bạn vào → tìm `forward_from_chat` → `id` (dạng `-100…`). Điền vào `ALLOWED_GROUP_IDS`.

> Bot sẽ **tự rời** mọi group không nằm trong `ALLOWED_GROUP_IDS` (trừ khi đang ở chế độ học id).

---

## 4. Cấu hình & biến môi trường

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `BOT_TOKEN` | ✔ | Token từ @BotFather |
| `BOT_USERNAME` | tùy chọn | Username bot — khi khởi động bot **tự lấy từ Telegram** (`getMe`); giá trị này chỉ là fallback |
| `ALLOWED_GROUP_IDS` | ✔ | Danh sách chat_id group được phép, cách nhau `,` |
| `ADMIN_IDS` | | user_id admin (dùng `/status`) |
| `LEARN_GROUP_ID_MODE` | | `1` = học chat_id thay vì tự rời group lạ |
| `GEMINI_API_KEY` | ✔ (1 key tối thiểu) | Nguồn AI chính (free ~1.500 req/ngày) |
| `GROQ_API_KEY` | khuyến nghị | Fallback khi Gemini lỗi/quá tải |
| `OPENROUTER_API_KEY` | tùy chọn | Fallback cuối |
| `SEARCH_BACKEND` | | `ddgs` (mặc định) · `searxng` · `tavily` |
| `SEARXNG_URL` | khi dùng searxng | URL nội bộ bot gọi tới SearXNG, mặc định `http://searxng:8080` (Docker network) |
| `SEARXNG_IMAGE` | | tùy chọn: pin image SearXNG theo tag ngày khi production (mặc định `ghcr.io/searxng/searxng:latest`) |
| `GRANIAN_BLOCKING_THREADS` | | Python blocking threads/worker của SearXNG (mặc định `1`, dành cho VPS nhỏ) |
| `GRANIAN_BACKPRESSURE` | | Giới hạn request đồng thời/worker SearXNG (mặc định `2`; giữ 1 worker) |
| `TAVILY_API_KEY` | khi dùng tavily | app.tavily.com (free ~1.000 credit/tháng) |
| `MAX_QUESTIONS_PER_MIN_PER_USER` | | Chống spam (mặc định 3) |
| `LOG_LEVEL` | | `INFO` mặc định |

---

## 5. Bật SearXNG production (private — chỉ cho bot, không public)

Cấu hình SearXNG trong repo đã được dựng sẵn theo hướng **production, private**:

- bot gọi nội bộ qua Docker network: `http://searxng:8080`
- container SearXNG **không publish port ra host/Internet**; chỉ dành cho mạng tin cậy
  (host/container cùng network vẫn có thể truy cập); không cần Valkey / reverse proxy / domain
- đặt rõ `limiter: false`, `public_instance: false`; vẫn mount `limiter.toml` vì
  SearXNG đọc cấu hình botdetection ngay cả khi limiter tắt
- mẫu loại `ahmia`/`torch` (không dùng Tor), tạm loại `startpage`/`wikipedia` do lỗi
  upstream đã báo; có thể bật lại hai engine sau khi cập nhật image và kiểm tra
- giới hạn Granian cho VPS nhỏ, không dùng biến `UWSGI_*`
- hướng dẫn đầy đủ và giải thích từng log: [DEPLOY_SEARXNG_VPS.md](DEPLOY_SEARXNG_VPS.md)

### 5.1. Tạo file config thật và đổi secret

```bash
# Chỉ dành cho cài mới; không ghi đè file đang có secret thật
cp -i searxng/settings.example.yml searxng/settings.yml
openssl rand -hex 32
nano searxng/settings.yml       # đổi secret_key bằng kết quả lệnh trên
```

**Đã có `settings.yml`?** Merge thay đổi từ mẫu, giữ secret và recreate container;
chỉ cập nhật file mẫu hoặc `restart` là chưa đủ để áp dụng mount/env mới.
Xem mục 3.2 trong [hướng dẫn nâng cấp](DEPLOY_SEARXNG_VPS.md).

### 5.2. Điền trong `.env`

```env
SEARCH_BACKEND=searxng
SEARXNG_URL=http://searxng:8080
```

### 5.3. Chạy & kiểm tra

```bash
docker compose --profile searxng up -d --build

docker compose --profile searxng ps
docker compose logs --tail=100 searxng
docker compose logs --tail=100 bot

# healthcheck nội bộ (không cần mở port ra host):
docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/healthz
```

---

## 6. Cách dùng trong group

| Cách gọi | Ví dụ |
|---|---|
| @mention | `@FuckingCoolAIbot giá vàng hôm nay bao nhiêu?` |
| Reply tin của bot / của thành viên + tag | reply tin cũ: `thế còn ở VN thì sao? @FuckingCoolAIbot` |
| Lệnh `/ask` | `/ask giải thích ngắn blockchain là gì` |
| `/help`, `/status` (admin) | trợ giúp / trạng thái vận hành |

Bot **tự quyết định** khi nào cần tìm web: câu hỏi thời sự/giá cả/thời tiết → tự search
và đính **📚 Nguồn tham khảo**; câu hỏi khái niệm/tính toán/suy luận → trả lời thẳng.

**Giới hạn an toàn:** chỉ hoạt động trong `ALLOWED_GROUP_IDS` (tự rời group lạ);
mỗi người ≤ `MAX_QUESTIONS_PER_MIN_PER_USER` câu/phút; câu hỏi > 4.000 ký tự bị cắt.
Tool đọc web chỉ cho phép URL public (chặn IP nội bộ/localhost — chống SSRF).

> 🔒 **Quyền riêng tư:** nội dung câu hỏi được gửi tới provider AI miễn phí
> (Gemini/Groq/OpenRouter). Theo điều khoản free tier, dữ liệu **có thể được dùng
> để huấn luyện model**. Khuyến cáo không hỏi thông tin bí mật/cá nhân trong group.

---

## 7. Vận hành

```bash
docker compose ps                        # trạng thái
docker compose logs --tail=100 bot       # log gần nhất của bot
docker compose logs --tail=100 searxng   # log SearXNG
# Healthcheck SearXNG nội bộ (không mở port ra host — dùng exec):
docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/healthz
docker compose restart bot               # khởi động lại bot
docker compose up -d --build             # cập nhật code mới
docker compose --profile searxng down    # tắt bot + searxng profile
```

Không có database — muốn "backup" chỉ cần giữ bản sao `.env`. Khi `GEMINI_API_KEY` hết
quota (log báo 429), bot tự chuyển sang Groq/OpenRouter nếu đã cấu hình.

---

## 8. Xử lý sự cố thường gặp

| Triệu chứng | Nguyên nhân & cách xử lý |
|---|---|
| Bot im lặng trong group | `ALLOWED_GROUP_IDS` chưa đúng chat_id (kiểm tra log `GROUP_ID_LEARN`); hoặc Privacy Mode vẫn ON (gỡ + add lại bot) |
| Log báo `BOT_TOKEN không hợp lệ` | Sai token; tạo lại ở @BotFather |
| Log báo `Thiếu/Chưa cấu hình API key` | Điền `GEMINI_API_KEY` (hoặc Groq/OpenRouter) vào `.env` rồi restart |
| Trả lời "không tìm kiếm được web" | Backend `ddgs` bị chặn tạm thời → bật SearXNG (mục 5) hoặc Tavily |
| Granian cảnh báo `spawning up to 4 Python threads` | Compose mới dùng 1 blocking thread + backpressure 2 cho VPS nhỏ; cần recreate để nhận env mới |
| Log SearXNG báo `missing config file: /etc/searxng/limiter.toml` | Botdetection vẫn đọc file dù limiter tắt; mount `searxng/limiter.toml` có sẵn rồi recreate |
| Log SearXNG báo thiếu `X-Forwarded-For` / `X-Real-IP` | Có thể chấp nhận với kết nối trực tiếp private và limiter/public_instance đều tắt; không giả IP ở bot. Nếu dùng proxy/public phải cấu hình header đúng (xem tài liệu triển khai) |
| `ahmia`/`torch` không load; Startpage parse JSON lỗi; Wikipedia HTTP 400 | Mẫu mới loại các engine này (hai engine sau là workaround). Phải merge vào **file thật** `settings.yml`; xem chẩn đoán `unresponsive_engines` ở tài liệu triển khai |
| Log SearXNG báo `... settings.yml is not a valid file` | Chưa tạo `searxng/settings.yml` từ `settings.example.yml` (mục 5.1) |
| Bị 429 khi nhóm dùng nhiều | Hết quota Gemini phút/ngày → tự fallback; bớt tần suất hoặc thêm key Groq/OpenRouter |

---

## 9. Cấu trúc repo

```
app/
├── main.py              # khởi động bot (polling)
├── config.py            # đọc .env (pydantic-settings)
├── bot/                 # filters (allowlist, trigger) + handlers + lifecycle (tự rời group lạ)
├── core/                # orchestrator (tool-calling), context, rate-limit, stats, formatting
├── ai/                  # provider Gemini/Groq/OpenRouter + router fallback
└── search/              # backend ddgs/searxng/tavily + reader (Jina/BS4)
Dockerfile · docker-compose.yml · requirements.txt · .env.example
searxng/settings.example.yml · searxng/limiter.toml
DEPLOY_SEARXNG_VPS.md
```

Chi tiết thiết kế, hạn mức free tier & lộ trình: xem [PLAN_TRIEN_KHAI.md](PLAN_TRIEN_KHAI.md).

---

## 10. Chạy bộ kiểm thử (audit, offline — không cần mạng/key)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python tests/run_tests.py     # kỳ vọng: 194 passed, 0 failed
```

Bộ test gồm: config/formatting/context/rate-limit/stats · filters aiogram ·
guard chống SSRF (IP literal nội bộ, dạng viết tắt `127.1`/`2130706433`/`0x7f…`,
IPv6 zone index & IPv4-mapped, dải CGNAT/unspecified/broadcast/link-local,
DNS-rebinding/`nip.io`, IP pinning — resolve trước và chỉ kết nối IP công khai,
kiểm tra lại từng chặng redirect, chặn redirect loop, giới hạn dung lượng
trang, Jina-reader 200/404) · **E2E handlers qua `Dispatcher.feed_update`**
(fake Telegram session: /help, /ask, /ask@bot đúng/sai mention, mention trong
caption, reply-tin-bot, group lạ/private im lặng, non-admin /status im lặng, tự
rời group/channel lạ qua `my_chat_member`, learn-mode không rời, rate-limit
chống spam, AllProvidersFailed/lỗi lạ/answer-rỗng) · **AI router** với mock
OpenAI server (fallback 429, tool-calling loop, retry-không-tools, tool-loop
kẹt vòng tự retry không-tools đúng giới hạn, unsupported-tools ở cả 2 pass,
tool call thiếu `id` tự sinh id thay thế, AllProvidersFailed) · **search
backends** (Tavily `Authorization: Bearer`, SearXNG JSON API, metadata lỗi
`unresponsive_engines` không làm mất kết quả tốt, không giả IP forwarded,
HTTP 403/JSON không hợp lệ vẫn báo lỗi) · **main
fail-fast** (thiếu BOT_TOKEN/AI key dừng ngay, không gọi mạng).
