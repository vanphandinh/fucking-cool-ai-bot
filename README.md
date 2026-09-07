# 🤖 fucking-cool-ai-bot

Bot Telegram AI chạy trong **group được chỉ định**, deploy bằng **Docker Compose** trên VPS.
Trả lời bằng **AI miễn phí** (Google Gemini → Groq → OpenRouter `:free`), có **tìm kiếm web**
(DuckDuckGo / SearXNG / Tavily), chỉ nói chuyện với các group nằm trong danh sách cho phép.

> 📄 Kế hoạch triển khai chi tiết: [PLAN_TRIEN_KHAI.md](PLAN_TRIEN_KHAI.md)
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
| `SEARXNG_URL` | khi dùng searxng | `http://searxng:8080` |
| `TAVILY_API_KEY` | khi dùng tavily | app.tavily.com (free ~1.000 credit/tháng) |
| `MAX_QUESTIONS_PER_MIN_PER_USER` | | Chống spam (mặc định 3) |
| `LOG_LEVEL` | | `INFO` mặc định |

---

## 5. Bật thêm SearXNG (tùy chọn — tìm kiếm không giới hạn, có engine Google)

```bash
# 1) Tạo settings thật từ mẫu và đổi secret_key
mkdir -p searxng && cp searxng/settings.example.yml searxng/settings.yml
nano searxng/settings.yml        # đổi secret_key bằng chuỗi ngẫu nhiên 32+ ký tự

# 2) Chạy kèm profile searxng
docker compose --profile searxng up -d --build

# 3) Trong .env: SEARCH_BACKEND=searxng và SEARXNG_URL=http://searxng:8080
docker compose up -d
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
docker compose logs --tail=100 bot       # log gần nhất
docker compose restart bot               # khởi động lại
docker compose up -d --build             # cập nhật code mới
docker compose --profile searxng down    # tắt cả searxng
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
searxng/settings.example.yml
```

Chi tiết thiết kế, hạn mức free tier & lộ trình: xem [PLAN_TRIEN_KHAI.md](PLAN_TRIEN_KHAI.md).

---

## 10. Chạy bộ kiểm thử (audit, offline — không cần mạng/key)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python tests/run_tests.py     # kỳ vọng: 97 passed, 0 failed
```

Bộ test gồm: config/formatting/context/rate-limit/stats · filters aiogram ·
guard chống SSRF (IP literal nội bộ, dạng viết tắt `127.1`/`2130706433`/`0x7f…`,
IPv6 zone index & IPv4-mapped, DNS-rebinding/`nip.io`, IP pinning — resolve trước
và chỉ kết nối IP công khai, kiểm tra lại từng chặng redirect, giới hạn dung
lượng trang) · **E2E handlers qua `Dispatcher.feed_update`** (fake Telegram
session: /help, /ask, /ask@bot đúng/sai mention, mention trong caption,
reply-tin-bot, group lạ/private im lặng, non-admin /status im lặng, tự rời
group/channel lạ qua `my_chat_member`, learn-mode không rời) · **AI router** với
mock OpenAI server (fallback 429, tool-calling loop, retry-không-tools, tool-loop
kẹt vòng tự retry không-tools đúng giới hạn, AllProvidersFailed).
