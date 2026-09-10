# X/Twitter content fetching

Tài liệu này mô tả direct X/Twitter URL flow hiện tại của bot. Mục tiêu là đọc public status/thread theo
hướng **free-first**, tránh vòng `web_search(raw X URL) -> mirror search -> nhiều tool/model rounds` đã từng
làm tăng latency và đốt provider quota.

## 1. Runtime path

Khi user đưa một public X/Twitter status URL cụ thể và hỏi nội dung URL đó, system policy yêu cầu AI dùng
`fetch_url` **trước** `web_search`.

### `mode=auto`

```text
X/Twitter status URL
  -> validate public URL
  -> parse + canonicalize
  -> FxTwitter API v2 GET /2/status/{id}
  -> nếu fail: official X oEmbed
  -> nếu fail và Crawl4AI active: Crawl4AI POST /crawl với canonical X URL
  -> nếu fail/disabled: generic SSRF-safe reader
  -> nếu vẫn không đủ: AI mới có thể quyết định web_search
```

### `mode=x_thread`

```text
X/Twitter status URL
  -> validate public URL
  -> FxTwitter API v2 GET /2/thread/{id}
  -> nếu fail: focal GET /2/status/{id}
  -> nếu fail: X oEmbed
  -> nếu fail và Crawl4AI active: Crawl4AI POST /crawl
  -> nếu fail/disabled: generic reader
```

Nếu thread endpoint fail nhưng focal status đọc được, tool output nói rõ `Full thread unavailable; focal post only.`
Không được giả vờ đã đọc toàn thread.

AI tool surface vẫn chỉ có ba tool:

- `web_search(query)`
- `image_search(query)`
- `fetch_url(url, mode?)`

Không thêm X-specific tool name để giảm tool-choice ambiguity.

## 2. Supported status URLs

Specialized parser hiện nhận các host:

- `x.com`, `www.x.com`
- `twitter.com`, `www.twitter.com`
- `mobile.twitter.com`, `m.twitter.com`
- `fxtwitter.com`, `www.fxtwitter.com`
- `fixupx.com`, `www.fixupx.com`

Chỉ URL có path `/status/<id>` với status ID số từ 2 đến 20 chữ số mới được specialized. Ví dụ:

```text
https://x.com/TrustlessState/status/2097054140350554620
https://twitter.com/user/status/1234567890?ref=abc
https://mobile.twitter.com/user/status/1234567890/photo/1
https://x.com/i/status/1234567890
```

Mirror/legacy URL hợp lệ được canonicalize về `https://x.com/.../status/<id>` trước khi specialized fetch và trước fallback URL reading.

Không nhận dạng specialized cho:

```text
https://x.com/user
https://x.com.evil.example/user/status/1234567890
https://example.org/user/status/1234567890
https://x.com/user/status/not-a-number
```

## 3. Safety model

- `read_url()` chạy `reader.validate_public_url()` trước specialized X resolution và trước mọi downstream reader.
- User URL chỉ được dùng để lấy allowlisted host, handle và numeric status ID.
- Specialized upstream hosts được **hard-code** trong code:
  - `https://api.fxtwitter.com`
  - `https://publish.x.com`
- User input không thể thay host API egress.
- Source hiển thị cho user luôn là canonical X URL, không phải API/mirror URL.
- Crawl4AI fallback nhận canonical X URL và vẫn nằm sau public-URL validation; Crawl4AI client không nhận arbitrary browser/hooks/model config từ user.
- Generic fallback dùng cùng canonical URL và các DNS/redirect SSRF guard + body/deadline limits của generic reader.
- Deceptive host như `x.com.evil.example` không được specialized.
- Redirect follow ở specialized HTTP client bị tắt.

## 4. Output normalization và limits

FxTwitter status output chỉ giữ dữ liệu hữu ích cho LLM:

- author name/handle
- created time nếu có
- post text
- likes/reposts/quotes/replies/views nếu có
- media type và alt text
- quoted post text/author khi có

Không đưa raw media/video CDN URL vào tool text.

Limits:

- specialized resolver total deadline: `min(12s, REQUEST_TIMEOUT_SEC)`; tối thiểu 1 giây;
- X tool content tối đa 5500 ký tự trước router cap hiện có;
- thread tối đa 12 post;
- focal/thread duplicate status ID được dedupe;
- nếu thread dài hơn giới hạn, output ghi `Thread truncated to 12 posts.`;
- duplicate `(url, mode)` trong cùng một Telegram question dùng request-local cache ở orchestrator/tool layer.

## 5. Failure semantics

`UrlReadResult` có bốn trường:

```text
text
source_url
backend
ok
```

Backend có thể là:

```text
fxtwitter_status
fxtwitter_thread
x_oembed
crawl4ai
generic_reader
blocked
invalid
```

Chỉ `ok=True` mới làm orchestrator:

- set `searched=True` cho answer metadata;
- append source URL.

Specialized X failure tự nó chưa tạo failure result: runtime tiếp tục sang Crawl4AI nếu active rồi generic reader. Chỉ khi pipeline URL reading cuối cùng không đọc được thì model nhận failure text để quyết định bước tiếp theo; failure không tạo source giả.

`mode=x_thread` trên non-X URL trả invalid result ngay sau public-URL validation và không gọi network thread endpoint/Crawl4AI/generic reader.

`CancelledError` không bị đổi thành X/Crawl4AI failure: cancellation propagate và không khởi chạy generic fallback mới.

## 6. AI policy chống search loop

Khi user đã đưa URL cụ thể cần đọc/tóm tắt/giải thích:

```text
fetch_url(URL) trước web_search
```

Với direct X status URL đã fetch thành công và đủ dữ liệu, AI được yêu cầu không search lại:

- raw X URL
- `fxtwitter.com/...`
- `fixupx.com/...`
- nitter mirror
- exact quote chỉ để tìm lại cùng post

Search vẫn hợp lệ nếu direct URL pipeline thất bại/không đủ hoặc user yêu cầu kiểm chứng thêm từ nguồn khác.

## 7. Configuration và rollback

Default:

```dotenv
X_FETCH_ENABLED=1
MAX_TOOL_ROUNDS=2
REQUEST_TIMEOUT_SEC=60.0
```

`X_FETCH_ENABLED=1` không cần API key.

Rollback specialized resolver:

```dotenv
X_FETCH_ENABLED=0
```

Sau đó recreate bot:

```bash
docker compose --profile searxng up -d --build --force-recreate bot
```

Khi flag tắt, bot bỏ FxTwitter/oEmbed specialized path. URL vẫn đi qua URL-reading pipeline chung: Crawl4AI nếu được active, sau đó generic reader. Muốn rollback cả Crawl4AI thì đặt thêm:

```dotenv
CRAWL4AI_ENABLED=0
```

**Không tăng `MAX_TOOL_ROUNDS` để chữa fetch failure.** Production baseline của repo là `2` để hạn chế
model/tool amplification và provider quota pressure.

## 8. Deploy / upgrade checklist

Sau `git pull`/merge, `.env` cũ không tự nhận default mới. Dùng env sync helper rồi kiểm tra effective values:

```bash
python scripts/sync_env.py
grep -E '^(X_FETCH_ENABLED|CRAWL4AI_ENABLED|MAX_TOOL_ROUNDS|REQUEST_TIMEOUT_SEC)=' .env || true
```

Khuyến nghị:

```dotenv
X_FETCH_ENABLED=1
MAX_TOOL_ROUNDS=2
REQUEST_TIMEOUT_SEC=60.0
```

Recreate bot:

```bash
docker compose --profile searxng up -d --build --force-recreate bot
```

Không cần thay đổi `searxng/settings.yml` chỉ để bật direct-X resolver.

## 9. VPS smoke test

### Direct status

```bash
docker compose --profile searxng exec -T bot python - <<'PY'
import asyncio
from app.config import Settings
from app.search.url_service import read_url

URL = "https://x.com/TrustlessState/status/2097054140350554620"
result = asyncio.run(read_url(URL, Settings(), mode="auto"))
print("ok:", result.ok)
print("backend:", result.backend)
print("source:", result.source_url)
print("preview:", result.text[:300].replace("\n", " "))
PY
```

Nếu post vẫn public, normal success thường là:

```text
ok: True
backend: fxtwitter_status
```

hoặc fallback `x_oembed`; nếu specialized sources fail thì `crawl4ai` (khi active) hoặc `generic_reader` cũng là fallback hợp lệ.

`source` của specialized success vẫn phải là canonical `https://x.com/...`; downstream readers cũng nhận canonical X URL.

### Thread

```bash
docker compose --profile searxng exec -T bot python - <<'PY'
import asyncio
from app.config import Settings
from app.search.url_service import read_url

URL = "https://x.com/TrustlessState/status/2097054140350554620"
result = asyncio.run(read_url(URL, Settings(), mode="x_thread"))
print("ok:", result.ok)
print("backend:", result.backend)
print(result.text[:1000])
PY
```

Nếu full thread không available nhưng focal post có, output phải nói rõ focal-only degradation. Nếu toàn specialized path fail, fallback URL reader chỉ đọc page content chứ không được giả vờ là full thread.

## 10. Telegram smoke test và log expectations

Gửi bot một public X status URL kèm yêu cầu tóm tắt/giải thích, sau đó:

```bash
docker compose logs --since=5m bot searxng
```

Khi direct fetch thành công, kỳ vọng:

- không có SearXNG query `q=` là raw X URL trước direct fetch;
- không search fxtwitter/nitter/fixupx mirror để tìm lại post;
- cùng `(URL, mode)` không bị fetch lặp trong một update;
- request đơn giản thường chỉ cần model tool turn + final answer, thay vì nhiều tool/model rounds;
- `MAX_TOOL_ROUNDS` vẫn là `2`.

Nếu thấy raw X URL bị search trước fetch, kiểm tra bot image/code đã update và effective env:

```bash
docker compose exec -T bot python - <<'PY'
from app.config import Settings
s = Settings()
print("X_FETCH_ENABLED =", s.x_fetch_enabled)
print("CRAWL4AI_ENABLED =", s.crawl4ai_enabled)
print("MAX_TOOL_ROUNDS =", s.max_tool_rounds)
PY
```

## 11. SearXNG relationship

Direct X/Twitter fetch và SearXNG search là hai luồng khác nhau:

```text
fetch_url(X status) -> FxTwitter/oEmbed -> optional Crawl4AI -> generic reader
web_search(query)   -> SearXNG/DDGS
```

Vì vậy:

- FxTwitter/oEmbed/Crawl4AI failure không tự động có nghĩa SearXNG hỏng;
- SearXNG engine failure không tự động có nghĩa direct-X hỏng;
- không sửa engine SearXNG chỉ vì direct-X resolver fail;
- không test direct-X bằng cách search raw X URL.

Xem thêm [../DEPLOY_SEARXNG_VPS.md](../DEPLOY_SEARXNG_VPS.md),
[CRAWL4AI_INTEGRATION.md](CRAWL4AI_INTEGRATION.md) và
[SEARXNG_DDG_INCIDENT_2026-09-08.md](SEARXNG_DDG_INCIDENT_2026-09-08.md).

## 12. Deferred adapters

Initial integration cố ý **không** thêm:

- official paid X API credentials/billing
- Firecrawl
- Bright Data
- Apify
- TwitterAPI.io
- self-host FxEmbed credentials/session handling

Nếu production metrics cho thấy FxTwitter + oEmbed + current URL-reading fallbacks chưa đủ ổn định, adapter mới nên đi sau `UrlReadResult`
interface hiện tại để orchestrator/tool contract không phải đổi lần nữa.
