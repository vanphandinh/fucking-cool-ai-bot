# KẾ HOẠCH TRIỂN KHAI — Bot Telegram AI cho Group (Docker Compose)

> **Trạng thái:** Kế hoạch đã được chốt theo các quyết định ở mục 0 & 16.
> ✅ **Triển khai code Phase P1–P2 đã hoàn tất (2026-09-07)** — cấu trúc repo theo mục 6, chạy bằng `docker compose up -d --build` (xem README.md).
> ✅ **Audit toàn diện đã thực hiện (2026-09-07)** — sửa lỗi nghiêm trọng (my_chat_member, caption, username động, đóng tài nguyên, SSRF guard…) + 118/118 kiểm thử tự động offline (`python tests/run_tests.py`). Đợt audit sâu lần 2 bổ sung: vá **SSRF** qua IP viết tắt (127.1/2130706433/0x7f…), IPv6 zone index & IPv4-mapped, **DNS-rebinding** bằng **IP pinning** (resolve trước, chỉ kết nối tới IP công khai — đóng TOCTOU lúc httpx resolve lần 2) và kiểm tra lại từng chặng redirect; sửa **vòng lặp tool bị kẹt** (retry không-tools đúng số vòng, không tắt tools vĩnh viễn); trigger mention theo đúng ranh giới username; `/ask@BotKhác` bị bỏ qua; **graceful shutdown** SIGTERM/SIGINT; fail-fast khi gõ sai `SEARCH_BACKEND`; dọn dead code (ChatMemory.clear, hằng số UA chết, giá trị trả về thừa trong router); thêm **pyproject.toml chuẩn ruff** (line-length 100, format toàn repo).
> ⏳ **Còn lại:** cấu hình `.env` trên VPS + chạy thử nghiệm theo checklist mục 15.

---

## 0. Quyết định đã chốt (từ yêu cầu)

| # | Chủ đề | Quyết định |
|---|--------|-----------|
| 1 | Stack | **Python 3.12 + aiogram v3** (async, phổ biến, docs tốt) |
| 2 | Nguồn AI | **Đa nguồn miễn phí + tự fallback**: Gemini (chính) → Groq → OpenRouter `:free` |
| 3 | Hạ tầng | **VPS Linux** chạy Docker Compose |
| 4 | Cách bot được gọi | **Hỗn hợp**: lệnh `/ask <câu hỏi>`, @mention bot, hoặc reply vào tin (của bot **hoặc của thành viên khác**) kèm tag bot — **cần Privacy Mode = OFF** (hoặc bot làm admin) để bot đọc được nội dung tin được reply (xem mục 5.4) |
| 5 | Phạm vi | Chỉ hoạt động trong **các group được chỉ định** (allowlist theo chat_id), không chạy ngoài phạm vi; bị thêm vào group không trong danh sách → **tự rời group** (mục 5.5) |
| 6 | Chi phí | AI = **0đ** (free tier). Chỉ tốn tiền VPS |
| 7 | Tên bot | **@FuckingCoolAIbot** — đã tạo, Privacy Mode = OFF, **đã add vào 1 group riêng tư** |
| 8 | Ngôn ngữ | Trả lời mặc định **tiếng Việt** (không tự phát hiện ngôn ngữ) |
| 9 | Ai được hỏi | **Toàn bộ thành viên** trong các group allowlist |
| 10 | Mức suy luận | **Suy luận thông thường** (giải thích, tính toán, phân tích đơn giản — không cần "deep reasoning") |
| 11 | Quy mô nhóm | Nhóm riêng tư **< 20 thành viên** → free tier dư sức (~1.500 req/ngày) |

---

## 1. Tóm tắt mục tiêu

Xây dựng một **Telegram bot thông minh**:
- Được thêm vào 1–N group Telegram **chỉ định trước** (allowlist), **tự động bỏ qua** mọi chat khác.
- Trả lời câu hỏi của thành viên group bằng **AI miễn phí** (chat thường, giải thích, suy luận đơn giản, tính toán…).
- Khi câu hỏi cần dữ liệu mới (tin tức, thời sự, thông số hiện tại…) thì **tự tìm kiếm web**, đọc nội dung và **tóm tắt kèm nguồn**.
- Chạy ổn định 24/7 bằng **Docker Compose**, chi phí vận hành ≈ 0đ (chỉ tiền VPS).

**Không nằm trong phạm vi (v1):** nhận diện giọng nói, tạo ảnh, hội thoại bộ nhớ dài hạn nhiều ngày, tích hợp nhiều chat platform.

---

## 2. Kiến trúc tổng quan

```
Thành viên group Telegram
   │  @TenBot / reply tin của bot / /ask <câu hỏi>
   ▼
┌─────────────────────────────────────┐
│  CONTAINER: telegram-bot (aiogram v3)│
│  1. Lọc allowlist chat_id           │
│  2. Rate limit chống spam           │
│  3. Xác định trigger + context      │
│  4. Orchestrator (tool-calling)     │
│  5. Chuỗi provider + fallback       │
└────────────┬──────────────┬─────────┘
             │              │
     gọi search tool        │  prompt / trả lời
             ▼              ▼
┌──────────────────┐  ┌──────────────────────────────────────┐
│ SEARCH layer     │  │ AI PROVIDER LAYER (free tier)        │
│ (tùy chọn theo   │  │  1) Google Gemini API  <- chính      │
│  cấu hình)       │  │  2) Groq               <- fallback 1 │
│  S1 ddgs         │  │  3) OpenRouter :free   <- fallback 2 │
│  S2 SearXNG      │  │  4) Cloudflare Workers AI (dự phòng) │
│  S3 Tavily       │  └──────────────────────────────────────┘
└──────────────────┘
```

**Nguyên tắc kiến trúc:**
- **Provider-agnostic**: cả 3 nguồn AI đều nói chung một "ngôn ngữ" (OpenAI-compatible style tool-calling). Đổi/bổ sung provider = sửa 1 file cấu hình, không đụng logic bot.
- **Fallback tự động**: nếu Gemini trả lỗi `429 (rate limit)` / `5xx` / timeout → thử Groq → OpenRouter → mới thông báo lỗi cho user.
- **Tool-calling do model quyết định**: model tự nhận diện "câu hỏi này cần web" và gọi tool `search_web`. Câu hỏi suy luận/kiến thức cũ → trả lời thẳng, không search (tiết kiệm quota).
- **Trạng thái tối giản**: lịch sử hội thoại gần đây giữ trong bộ nhớ (LRU); không cần database ở v1.

---

## 3. Yêu cầu phi chức năng (đặc tả chất lượng)

| Yêu cầu | Mô tả |
|---|---|
| Giới hạn phạm vi | Chỉ reply trong `ALLOWED_GROUP_IDS`. Tin từ group khác / private chat / channel → im lặng bỏ qua |
| Chi phí | Tổng chi phí AI hàng tháng = 0đ. Không yêu cầu thẻ tín dụng |
| Tiếng Việt | Mặc định trả lời tiếng Việt, ngắn gọn, dễ đọc (system prompt) |
| Ổn định | Tự khởi động lại khi crash; không crash khi tin dài/emoji/caption/format kỳ lạ; không chặn cả bot khi 1 request lỗi |
| An toàn token | Token & key chỉ trong `.env` (không commit); container chạy user non-root |
| Tốc độ cảm nhận | Gửi trạng thái "đang trả lời…" ngay lập tức; timeout mỗi request AI ≤ 60s |
| Giới hạn dùng | Chống spam: mỗi user nhóm ≤ N câu hỏi/phút; độ dài câu hỏi ≤ 4.000 ký tự |
| Riêng tư | Không ghi log nội dung hội thoại ở mức debug; cảnh báo group về việc dữ liệu gửi cho bên thứ 3 (free tier có thể dùng dữ liệu để huấn luyện) |

---

## 4. Lựa chọn công nghệ & lý do

### 4.1. Nền bot

| Thành phần | Chọn | Lý do |
|---|---|---|
| Ngôn ngữ | Python 3.12 | Hệ sinh thái AI tốt nhất, code ngắn, dễ bảo trì |
| Framework | aiogram v3 | Async native, router/filter gọn, FSM, middleware, cộng đồng lớn |
| HTTP client | httpx (async) | Gọi AI/search không chặn event loop |
| Config | pydantic-settings + `.env` | Validate key, dễ test |
| Chạy | Docker + Compose | Tái lập môi trường 100%, dễ deploy/rollback |

### 4.2. Lớp AI (miễn phí) — chuỗi ưu tiên

Nghiên cứu thị trường free tier (2026-09, có thể thay đổi — mục 13):

| Ưu tiên | Provider | Model gợi ý | Hạn mức free (tham khảo) | Vai trò |
|---|---|---|---|---|
| 1 | **Google AI Studio (Gemini API)** | `gemini-2.5-flash` | ~1.500 req/ngày; 15–30 RPM; context 1M token; **không cần thẻ** | Trả lời chính + tool-calling. Chất lượng cao nhất trong nhóm free |
| 2 | **Groq** | Llama 3.3 70B / Qwen | ~14.400 req/ngày; 30 RPM; **không cần thẻ** | Fallback khi Gemini quá hạn mức hoặc lỗi. Rất nhanh |
| 3 | **OpenRouter `:free`** | DeepSeek R1 / Llama / Qwen | ~20 RPM; 50–1.000 req/ngày tùy tài khoản | Fallback cuối cùng; một key truy cập nhiều model |
| — | Cloudflare Workers AI | Llama 3.1 8B… | ~10.000 req/ngày | Dự phòng xa (chất lượng thấp hơn, chỉ cho task đơn giản) |

**Chiến lược quota theo ngày:** nếu Gemini chạm 1.500 req/ngày thì nhóm 30 người × 50 câu hỏi/ngày/người là **thừa sức**; khi hết, tự rơi xuống Groq → OpenRouter nên về lý thuyết "không bao giờ hết AI".

### 4.3. Lớp tìm kiếm web — 3 tầng (chọn theo cấu hình)

| Tầng | Giải pháp | Phí | Ghi chú |
|---|---|---|---|
| S1 (mặc định, đơn giản) | **DuckDuckGo** qua thư viện `ddgs` | 0đ, không cần key | Nhanh để chạy MVP; có thể bị giới hạn/rate-limit khi dùng nhiều |
| S2 (khuyến nghị cho chạy lâu dài) | **SearXNG tự host** — chạy **1 container riêng trong cùng compose** | 0đ, không giới hạn | Meta-search gom Google/Bing/…; tự chủ, không phụ thuộc quota bên ngoài; JSON API chính thức |
| S3 (tùy chọn, chất lượng cao) | **Tavily API** | Free ~1.000 credit/tháng, không cần thẻ | Search AI-native kèm snippet/extract; dùng khi S1/S2 kém |

Bổ sung: **đọc nội dung trang web** để tóm tắt → ưu tiên **Jina Reader (`https://r.jina.ai/<url>`)**, không cần key (~20 RPM), fallback tự parse HTML bằng `httpx + selectolax/BeautifulSoup`.

**Khuyến nghị cấu hình chuẩn:** `SEARCH_BACKEND=ddgs` lúc MVP → chuyển `searxng` khi cần ổn định → bật Tavily nếu muốn chất lượng tốt nhất.

> **❓ Google Search — trả lời trực tiếp:**
> - **API Google Search chính thức (Custom Search JSON API) KHÔNG được chọn**: free tier cũ (~100 query/ngày) đã **đóng cho khách mới** và lịch **ngừng phục vụ 01/01/2027** → không đáng phụ thuộc.
> - **Kết quả Google thì vẫn có**: backend **SearXNG (S2)** gom nhiều engine trong đó có **engine Google** → bot trả lời dựa trên kết quả Google mà **không tốn quota/key** của Google. Khi chọn `SEARCH_BACKEND=searxng`, mặc định bật engine `google` (+ `bing`, `duckduckgo`, `startpage` để dự phòng khi Google chặn/captcha).
> - Backend mặc định MVP là **DuckDuckGo (`ddgs`)** — nhanh, không cần key, nhưng index của DuckDuckGo là Bing (không phải Google).
> - **Kết luận thiết kế:** "có hỗ trợ Google Search" ở mức kết quả (qua SearXNG engine Google), không phải dùng API trả phí của Google.

### 4.4. Hạ tầng

| Thành phần | Chọn |
|---|---|
| VPS | Ubuntu 24.04 LTS, **1 vCPU / 2 GB RAM** (1 GB đủ nếu không chạy SearXNG) |
| Docker | Docker Engine + Compose plugin (bản mới nhất) |
| Compose services | `bot` (bắt buộc) + `searxng` (tùy chọn) + `redis` (tùy chọn, Phase 2) |
| Restart policy | `unless-stopped` + healthcheck + `init: true` |

> Redis **không bắt buộc ở v1**: rate-limit & context LRU chạy trong bộ nhớ tiến trình là đủ cho nhóm < 200 người. Chỉ thêm Redis khi có nhiều replica hoặc cần giữ context qua restart.

---

## 5. Chức năng & luồng xử lý chi tiết

### 5.1. Bề mặt lệnh (v1)

| Kích hoạt | Hành vi |
|---|---|
| `/ask <câu hỏi>` | Trả lời câu hỏi (không cần context, kiểu "hỏi 1 phát trả lời 1 phát") |
| `@TenBot <câu hỏi>` | Trả lời như trên (quen thuộc với user, không cần nhớ lệnh) |
| Reply vào **tin của bot** + hỏi | Bot hiểu **câu hỏi nối tiếp** → dùng context của lượt trước (hoạt động kể cả Privacy ON) |
| Reply vào **tin của thành viên khác** + tag @bot | Bot đọc cả tin được reply + câu hỏi mới → trả lời đúng ngữ cảnh (chỉ hoạt động khi Privacy OFF hoặc bot là admin — xem 5.4) |
| `/help` | Liệt kê cách dùng + giới hạn |
| `/status` | (chỉ admin) Provider đang dùng, quota ước tính, số câu hỏi hôm nay |

### 5.2. Pipeline xử lý một tin nhắn

```
1. Nhận update → kiểm tra: chat_id ∈ ALLOWED_GROUP_IDS ?
     Không → bỏ qua hoàn toàn (kể cả private chat).
2. Chỉ giữ message text/caption có trigger (bắt đầu bằng /ask, @bot,
   hoặc reply vào tin do bot gửi). Tin khác → bỏ qua (không tốn AI).
3. Rate limit theo (user_id): quá N câu/phút → từ chối nhẹ nhàng.
4. Trích xuất câu hỏi; dựng system prompt tiếng Việt; nạp context
   gần nhất (tối đa ~10 lượt) nếu là reply nối tiếp.
5. Gửi tín hiệu "đang soạn…" (typing) về group.
6. Orchestrator (model chính = Gemini) nhận prompt + khai báo tool:
     - Nếu model quyết định CẦN dữ liệu mới → gọi tool search_web(query)
       → SearchService chạy backend S1/S2/S3 → lấy 5–8 kết quả
       → (nếu cần) đọc nội dung trang qua Jina/parse HTML
       → đưa snippet + nguồn về cho model tóm tắt.
     - Nếu không cần (suy luận, kiến thức, tính toán…) → trả lời thẳng.
7. Khi model chính lỗi 429/5xx/timeout → retry 1 lần → chuyển provider
   Groq → OpenRouter (cùng prompt + tool kết quả nếu đã search).
8. Gửi câu trả lời: tách message > 4096 ký tự thành nhiều phần;
   kèm [nguồn](url) dạng link rút gọn, tên trang.
9. Lưu lượt vừa rồi vào context LRU (chỉ khi trong group allowlist).
```

### 5.3. Các ca sử dụng điển hình (kiểm thử sau này)

1. **Hỏi thường:** `/ask Giải thích ngắn gọn thế nào là blockchain?` → trả lời trực tiếp, không search.
2. **Hỏi cần web:** `@bot hôm nay giá vàng thế giới bao nhiêu?` → tự search, tóm tắt + link nguồn.
3. **Suy luận đơn giản:** `/ask Một cửa hàng giảm giá 20% rồi tăng 10%, net là bao nhiêu?` → tính toán + giải thích.
4. **Nối tiếp:** reply tin bot trước: "thế còn ở Việt Nam thì sao?" → hiểu ngữ cảnh, search tiếp.
5. **Ngoài group:** nhắn riêng bot hoặc nhóm khác → không phản hồi.

### 5.4. Bot đọc được những gì? (Privacy Mode & reply)

Theo cơ chế Telegram, khả năng bot đọc nội dung tin được reply phụ thuộc **Privacy Mode**:

| Tình huống | Privacy ON (mặc định, bot thường) | Privacy OFF hoặc bot làm admin |
|---|---|---|
| Reply vào **tin của bot** + hỏi | ✅ Đọc được — Telegram luôn đính kèm tin gốc (là tin của bot) | ✅ Đọc được |
| Reply vào **tin của thành viên khác** + tag @bot | ⚠️ Bot nhận **câu hỏi** nhưng **KHÔNG nhận nội dung tin gốc** (tin gốc không "visible" với bot → trường `reply_to_message` bị bỏ trống) | ✅ Đọc được cả tin gốc lẫn câu hỏi mới |
| Gõ `@bot …` hoặc `/ask …` trong group | ✅ Nhận được tin hỏi (không kèm context xung quanh) | ✅ Nhận được |
| Tin thường của thành viên (không tag, không reply bot) | ❌ Không nhận | ✅ Có nhận → bot **phải tự lọc trigger** (mục 5.2 bước 2) |

**Quyết định triển khai:** dùng **Privacy Mode = OFF** (`/setprivacy` → Disable) để bot đọc đúng ngữ cảnh khi thành viên reply tin của nhau rồi tag bot. Cách thay thế: giữ Privacy ON nhưng **promote bot làm admin** (admin luôn nhận mọi tin; cũng cần nếu muốn bot tự xóa tin spam ở Phase 2).

> ✅ **Trạng thái (2026-09-07):** đã **tắt Privacy Mode** qua BotFather — quyết định được chốt theo khuyến nghị này. **Lưu ý còn lại:** nếu bot đã được thêm vào group *trước* khi đổi privacy thì phải **gỡ bot khỏi group rồi thêm lại** để chế độ mới có hiệu lực.

> ⚠️ Thay đổi Privacy Mode qua BotFather **chỉ có hiệu lực sau khi gỡ bot khỏi group rồi thêm lại**. Ngoài ra bot **không có API đọc tin lịch sử** — ngữ cảnh chỉ gồm các update nhận được sau khi bật, nên code phải tự lưu context các lượt gần nhất (mục 5.2 bước 9).

### 5.5. Ràng buộc "chỉ hoạt động trong group được chỉ định" (enforcement cứng)

Yêu cầu bắt buộc: bot **không bao giờ** phản hồi bên ngoài các group nằm trong `ALLOWED_GROUP_IDS`. Áp dụng nhiều lớp bảo vệ:

| # | Lớp bảo vệ | Cơ chế |
|---|---|---|
| 1 | **Lọc đầu pipeline** | Mọi update đi qua bước 1 (mục 5.2): `chat_id ∉ ALLOWED_GROUP_IDS` → **bỏ qua ngay**, không tốn AI, không log nội dung |
| 2 | **Chặn private chat & channel** | Kể cả tin nhắn riêng từ admin chủ bot cũng không phản hồi (yêu cầu "chỉ trong group") |
| 3 | **Kiểm tra khi GỬI** | Trước mỗi lần gửi reply, xác nhận lại chat hiện tại vẫn nằm trong danh sách — phòng trường hợp group bị gỡ khỏi `.env` giữa chừng → bot ngừng ngay |
| 4 | **Tự rời group lạ** | Bắt sự kiện `my_chat_member` (bot bị thêm vào chat): nếu chat type là group/supergroup **không có trong danh sách** → bot **tự `leaveChat`** (rời group) và chỉ log + báo admin qua tin nhắn riêng nếu được phép |
| 5 | **Validate lúc khởi động** | Nếu `ALLOWED_GROUP_IDS` rỗng/sai định dạng → bot khởi động nhưng **không xử lý bất kỳ chat nào**, log cảnh báo rõ ràng |
| 6 | **Chat id là số âm của group** | Group/supergroup có chat_id âm (vd `-100…`) — lọc đúng loại chat tránh nhầm với user id dương |

*Ghi chú kiến trúc:* mọi handler (kể cả `/ask`, `/help`, `/status`) đều đặt **sau** bộ lọc này, nên không thể có đường vòng qua lệnh.

---

## 6. Cấu trúc dự án dự kiến (sẽ tạo khi bắt đầu code)



```
fucking-cool-ai-bot/
├── docker-compose.yml          # service: bot [+ searxng tùy chọn]
├── .env.example                # mẫu biến môi trường (commit được)
├── .env                        # THẬT — không commit (.gitignore)
├── .gitignore
├── Dockerfile                  # python:3.12-slim, chạy user non-root
├── README.md                   # hướng dẫn dùng
└── app/
    ├── main.py                 # khởi động bot, khai báo router
    ├── config.py               # đọc .env, validate (pydantic-settings)
    ├── bot/
    │   ├── filters.py          # lọc allowlist group, trigger @mention/reply
    │   ├── handlers.py         # /ask, /help, /status, on_mention
    │   └── middleware.py       # rate limit, typing indicator
    ├── core/
    │   ├── orchestrator.py     # prompt + tool-calling + fallback chain
    │   ├── context.py          # LRU lịch sử hội thoại
    │   └── formatting.py       # cắt message dài, định dạng link nguồn
    ├── ai/
    │   ├── base.py             # interface provider chung
    │   ├── gemini.py           # adapter Gemini API
    │   ├── groq.py             # adapter Groq (OpenAI-compatible)
    │   ├── openrouter.py       # adapter OpenRouter :free
    │   └── router.py           # chọn provider, phát hiện lỗi → fallback
    └── search/
        ├── service.py          # điều phối backend S1/S2/S3
        ├── ddgs_backend.py     # DuckDuckGo (không key)
        ├── searxng_backend.py  # gọi SearXNG JSON API (nếu dùng)
        ├── tavily_backend.py   # gọi Tavily (nếu cấp key)
        └── reader.py           # Jina Reader / parse HTML
```

**Trách nhiệm từng module:** (đã mô tả ở trên — chi tiết sẽ chốt ở Phase 1 khi code)

---

## 7. Biến môi trường (thiết kế `.env`)

| Biến | Bắt buộc | Ý nghĩa |
|---|---|---|
| `BOT_TOKEN` | ✔ | Token bot từ @BotFather |
| `ALLOWED_GROUP_IDS` | ✔ | Danh sách chat_id group được phép, dạng `-100111,-100222` |
| `ADMIN_IDS` | ✔ | user_id admin (lệnh `/status`, cấu hình nóng) |
| `GEMINI_API_KEY` | ✔ | Key Google AI Studio |
| `GROQ_API_KEY` | (khuyến nghị) | Key Groq — fallback 1 |
| `OPENROUTER_API_KEY` | tùy chọn | Key OpenRouter — fallback 2 |
| `TAVILY_API_KEY` | tùy chọn | Key Tavily — search S3 |
| `SEARXNG_URL` | tùy chọn | `http://searxng:8080` khi bật S2 |
| `SEARCH_BACKEND` | mặc định | `ddgs` \| `searxng` \| `tavily` |
| `CHAT_MODEL` | mặc định | tên model chính (vd `gemini-2.5-flash`) |
| `MAX_QUESTIONS_PER_MIN_PER_USER` | mặc định | chống spam (khuyến nghị 3) |
| `MAX_CONTEXT_TURNS` | mặc định | số lượt context tối đa (khuyến nghị 10) |
| `REQUEST_TIMEOUT_SEC` | mặc định | 60s |
| `LOG_LEVEL` | mặc định | `INFO` (không dùng DEBUG ở production — tránh lộ nội dung) |

> ⚠️ `ALLOWED_GROUP_IDS` là **rào chắn chính** của yêu cầu "chỉ chạy trong group được chỉ định" — phải kiểm tra ở đầu pipeline, không chỉ ở handler.

---

## 8. Các bước chuẩn bị tài khoản (thực hiện 1 lần, trước khi code xong)

### 8.1. Tạo bot Telegram
1. Mở chat **@BotFather** → `/newbot` → đặt tên + username (đuôi `bot`).
2. Lưu **token** (dạng `123456:ABC-...`) vào `.env`.
3. **Tắt Privacy Mode**: mở **@BotFather → /setprivacy → Disable**. Lý do: để bot đọc được nội dung tin khi thành viên reply tin của nhau và tag bot (xem mục 5.4). Sau khi đổi phải **gỡ bot khỏi group rồi thêm lại** thì mới có hiệu lực. *(Nếu muốn giữ Privacy ON: thay bằng cách promote bot làm admin — admin luôn nhận mọi tin.)*
4. Thêm bot vào group, **promote làm admin** *(tùy chọn, chỉ nếu muốn bot xóa tin spam sau này)*.

### 8.2. Lấy chat_id của group
- chat_id group dạng số **âm** (`-100…` cho supergroup). Cách lấy khi triển khai: (a) nhờ bot log sự kiện "được thêm vào group" kèm chat_id trong 10 phút đầu, hoặc (b) dùng bot `@userinfobot` forward 1 tin từ group vào để xem id. — Chi tiết thao tác sẽ nêu trong README khi code.

### 8.3. Cấp key AI (đều miễn phí, không cần thẻ)
1. **Gemini:** `aistudio.google.com/apikey` → Create API key (đăng nhập Google) → dán vào `GEMINI_API_KEY`. *(Kiểm tra hạn mức thực tế tại aistudio.google.com/rate-limit)*
2. **Groq:** `console.groq.com/keys` → tạo key → `GROQ_API_KEY`.
3. **OpenRouter** (tùy chọn): `openrouter.ai/keys` → `OPENROUTER_API_KEY`.
4. **Tavily** (tùy chọn): `app.tavily.com` → `TAVILY_API_KEY`.

### 8.4. VPS
- Mua VPS Ubuntu 24.04 (1 vCPU/2 GB là thoải mái), cài Docker Engine + Compose plugin theo docs chính thức. Cấu hình cơ bản: user `deploy` không phải root, mở port 22, bật firewall chỉ cho phép SSH (+ port 80 nếu muốn HTTPS cho SearXNG UI).

---

## 9. Runbook triển khai (tóm tắt — bản chi tiết viết ở Phase 1)

```
Bước 1  Clone repo về VPS, tạo .env từ .env.example, điền key.
Bước 2  docker compose up -d --build        # khởi động bot
Bước 3  docker compose ps                   # xác nhận health = healthy
Bước 4  docker compose logs -f bot          # theo dõi log khi test
Bước 5  Test trong group theo 5.3; test nhóm lạ → bot phải im lặng.
Bước 6  Cập nhật phiên bản mới:
        git pull && docker compose up -d --build
```

- Volume gắn: `.env` (read-only), thư mục log nếu có.
- Healthcheck: gửi request nội bộ / ping tiến trình; `restart: unless-stopped` đảm bảo bot tự sống lại sau khi VPS reboot.
- Dùng `docker compose config` để validate trước khi up.

---

## 10. Vận hành & giám sát

| Việc | Cách làm |
|---|---|
| Kiểm tra bot sống | `docker compose ps`; đặt cron check + gửi tin báo admin (tùy chọn) |
| Xem lỗi | `docker compose logs --tail=100 bot` |
| Theo dõi quota AI | `/status` trong group (admin): provider hiện tại, số request hôm nay, lần fallback gần nhất |
| Cập nhật | `git pull` + rebuild (mục 9, Bước 6) |
| Sao lưu | Không có database → chỉ cần giữ bản sao `.env` (nơi an toàn) + tag git |
| Reset khi "kẹt" | `docker compose restart bot` (không mất gì quan trọng) |
| Cảnh báo hết quota | Gemini free tier ngày ~1.500 req — nhóm < 200 người khó chạm; nếu chạm, log fallback sẽ báo |

---

## 11. Chi phí dự kiến

| Khoản | Chi phí |
|---|---|
| AI (Gemini/Groq/OpenRouter/SearXNG) | **0đ** (free tier) |
| Search (ddgs/SearXNG) | **0đ** |
| VPS | ~4–6 USD/tháng (1 vCPU/2 GB, phổ biến ở DigitalOcean/Vultr/Hetzner) |
| Domain/HTTPS | 0đ (không bắt buộc cho bot) |
| **Tổng** | **~5 USD/tháng** |

---

## 12. Rủi ro & phương án giảm thiểu

| # | Rủi ro | Mức | Đối phó |
|---|---|---|---|
| 1 | Free tier AI đổi chính sách / hết quota giữa tháng | Cao | Kiến trúc đa provider + fallback; key chỉ cấu hình là dùng được thêm nguồn |
| 2 | DuckDuckGo chặn IP datacenter / rate-limit | TB | Bật SearXNG container (nhiều engine), hoặc Tavily |
| 3 | Token/key bị lộ (commit nhầm, log) | Cao | `.env` trong `.gitignore`; không log nội dung; key quay vòng khi nghi ngờ; secret scanning (gitleaks) nếu dùng CI |
| 4 | Spam / lạm dụng trong group | TB | Rate limit theo user; giới hạn độ dài; quyền admin xóa tin (Phase 2) |
| 5 | Free tier dùng dữ liệu để huấn luyện | TB | Cảnh báo rõ trong group: không hỏi bí mật/cá nhân; có thể chọn Groq (policy khác) cho nhóm nhạy cảm |
| 6 | Model trả lời sai khi cần thông tin mới (không chịu search) | TB | Prompt ép model gọi tool khi không chắc chắn về dữ liệu hiện tại; kèm nguồn để user tự kiểm tra |
| 7 | Bot chết giữa đêm / VPS reboot | Thấp | `restart: unless-stopped`, healthcheck, auto-restart |
| 8 | Message dài > 4096 ký tự, định dạng lạ | Thấp | Module formatting tách tin; bọc mọi xử lý trong try/except |
| 9 | Telegram đổi API / aiogram thay đổi | Thấp | Pin version trong Dockerfile; cập nhật theo lịch |
| 10 | Privacy OFF → bot nhận mọi tin nhóm, dễ trả lời nhầm tin không được hỏi | TB | Lọc trigger chặt ở bước 2 pipeline: chỉ xử lý khi có `/ask`, mention bot, hoặc reply vào tin bot / tin có tag bot |
| 11 | Đổi Privacy Mode nhưng group cũ không áp dụng | Thấp | Sau khi `/setprivacy` phải **gỡ bot + thêm lại** vào group |
| 12 | Người lạ thêm bot vào group không có trong danh sách | TB | Lớp bảo vệ #4 (mục 5.5): bot **tự rời group** ngay khi bị add |
| 13 | Nhầm lẫn "Google Search" thành API trả phí của Google | Thấp | Plan dùng SearXNG engine Google / DuckDuckGo — không phụ thuộc Google API (mục 4.3) |
| 14 | SearXNG bị Google chặn/captcha (IP datacenter) | TB | Cấu hình nhiều engine dự phòng (bing, duckduckgo, startpage); fallback ddgs; tăng thời gian timeout; Tavily là lớp cuối |

---

## 13. Hạn mức free tier — tham khảo (kiểm tra lại ngay trước khi code)

> Số liệu thu thập 2026-09 từ các nguồn công khai, **có thể thay đổi bất kỳ lúc nào** — bước setup phải xác nhận lại trên dashboard chính thức.

| Nguồn | Hạn mức tham khảo |
|---|---|
| Google Gemini API | Flash ~1.500 req/ngày, 15–30 RPM, không cần thẻ; Pro bị giới hạn nặng hơn |
| Groq | ~14.400 req/ngày, 30 RPM, không cần thẻ |
| OpenRouter `:free` | ~20 RPM, 50–1.000 req/ngày tùy tài khoản; danh sách model xoay vòng |
| Tavily | ~1.000 credit/tháng, không cần thẻ |
| Brave Search API | Đã bỏ free tier trọn gói (chuyển credit ~1.000 truy vấn/tháng kèm điều kiện) — **không chọn** |
| SearXNG (tự host) | Không hạn mức (tự chịu hạ tầng) |
| Jina Reader | Không key: ~20 RPM cho đọc trang |
| DuckDuckGo `ddgs` | Không key; không cam kết SLA, có thể bị chặn khi dùng nhiều |

---

## 14. Lộ trình & phân bổ công việc (ước lượng 1 dev)

| Phase | Nội dung | Sản phẩm | Ước lượng |
|---|---|---|---|
| **P0 — Chuẩn bị** | Tạo bot, lấy key, mua VPS, cài Docker | Tài khoản + VPS sẵn sàng | 0,5 ngày |
| **P1 — MVP** | Cấu trúc dự án, allowlist, `/ask` + @mention, provider Gemini, fallback Groq, trả lời tiếng Việt, Dockerfile + compose | Bot chạy được trong group | 2–3 ngày |
| **P2 — Search** | Tool-calling, backend ddgs → SearXNG, đọc trang + tóm tắt có nguồn | Bot tự search web | 1–2 ngày |
| **P3 — Cứng hóa** | Rate limit, context nối tiếp, tách message dài, /status, test nhóm lạ, log sạch | Bot ổn định | 1–2 ngày |
| **P4 — Thử nghiệm** | Chạy thật trong group 1–2 tuần, theo dõi quota/lỗi | Báo cáo nghiệm thu | 2 tuần (theo dõi) |
| **P5 — Mở rộng (tùy chọn)** | Redis, admin xóa tin spam, voice trả lời, nhiều group phân quyền, tạo ảnh | Nâng cấp | theo nhu cầu |

**Tổng: ~5–8 ngày làm việc cho tới hết P3.**

---

## 15. Tiêu chí nghiệm thu (Definition of Done)

- [ ] Bot chạy bằng `docker compose up -d` trên VPS mới "sạch" (làm theo README từ đầu là chạy được).
- [ ] Chỉ trả lời trong các group trong `ALLOWED_GROUP_IDS`; thử ở group khác + private chat → **im lặng 100%**.
- [ ] `/ask`, @mention, reply-nối-tiếp hoạt động đúng theo mục 5.1–5.3.
- [ ] Reply vào tin của **thành viên khác** + tag bot → bot đọc đúng nội dung tin được reply và trả lời đúng ngữ cảnh (Privacy OFF).
- [ ] **Chỉ group được chỉ định:** ở group allowlist bot trả lời; ở group khác / private chat / channel → im lặng tuyệt đối; thử add bot vào group lạ → bot **tự rời** trong vòng vài giây.
- [ ] Câu hỏi cần dữ liệu mới (giá vàng, thời tiết, tin tức) → có kết quả web + link nguồn.
- [ ] Câu hỏi suy luận/tính toán → trả lời đúng logic, không search.
- [ ] Tắt `GEMINI_API_KEY` (mô phỏng hết quota) → bot tự chuyển Groq/OpenRouter, không crash.
- [ ] Bot sống lại sau khi `docker compose restart` và sau khi VPS reboot.
- [ ] `.env` không nằm trong git; log mức production không chứa nội dung hội thoại.
- [ ] Spam test: gửi 20 câu hỏi liên tiếp trong 1 phút → chỉ xử lý theo đúng giới hạn, không crash.

---

## 16. Thông số đã chốt (trả lời từ chủ dự án, 2026-09-07)

| # | Câu hỏi cũ | Chốt |
|---|---|---|
| 1 | Bot / group đầu tiên | Username **@FuckingCoolAIbot** (Privacy OFF), đã add vào 1 **group riêng tư**. Chat_id group sẽ được xác định trong bước khởi chạy đầu tiên (mục 8.2) rồi điền vào `ALLOWED_GROUP_IDS` |
| 2 | Ngôn ngữ trả lời | **Tiếng Việt mặc định** — không tự phát hiện ngôn ngữ (kể cả khi câu hỏi tiếng Anh, ưu tiên trả lời tiếng Việt trừ khi user yêu cầu khác) |
| 3 | Ai được hỏi | **Toàn bộ thành viên group** (không allowlist user) |
| 4 | Mức suy luận | **Thông thường** → model chính `gemini-2.5-flash` là đủ; không cần route sang model reasoning |
| 5 | Quy mô | Nhóm riêng tư **< 20 người** → nhu cầu ước tính ≤ ~50–100 câu/ngày, thấp hơn nhiều so với free tier |

---

## 17. Nguồn tham khảo (chính)

- Free tier Gemini API 2026 — tokenmix.ai/blog/gemini-api-free-tier-limits · precisionaiacademy.com (bài "Gemini API Free Tier")
- Tổng hợp free LLM API 2026 — klymentiev.com/blog/best-free-llm-2026 · tokenmix.ai/blog/chatgpt-api-alternative-free
- OpenRouter free models — klymentiev.com/blog/openrouter-free-tier · buldrr.com/openrouter-free-api-keys-free-models-simple-guide
- Free web search API cho AI agent — itechguides.com/7-free-web-search-apis-for-ai-agents-free-tiers-compared · codenote.net (Tavily alternatives) · github.com/openclaw/openclaw#16629 (Brave đổi chính sách)

---

*Tài liệu này là kế hoạch triển khai — chưa có mã nguồn nào được viết. Sau khi được duyệt, Phase P1 sẽ bắt đầu code theo cấu trúc ở mục 6.*
