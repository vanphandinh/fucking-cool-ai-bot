# SearXNG production trên VPS — private, chỉ cho bot Telegram

Instance trong repo này là **PRIVATE**: bot gọi JSON API qua Docker network và không publish cổng ra host/Internet. Không cần reverse proxy, domain hay Valkey cho mode này; `server.limiter` và `server.public_instance` được tắt rõ ràng.

> Direct X/Twitter status URL là luồng riêng. `fetch_url` ưu tiên FxTwitter v2 → X oEmbed → optional Crawl4AI → generic reader; không dùng lỗi direct-X làm lý do tăng `MAX_TOOL_ROUNDS` hay chỉnh engine SearXNG.

Xem thêm:

- [Search resilience](docs/SEARCH_RESILIENCE.md)
- [X/Twitter content fetching](docs/X_CONTENT_FETCHING.md)
- [Crawl4AI URL reading](docs/CRAWL4AI_INTEGRATION.md)
- [SearXNG DuckDuckGo incident 2026-09-08](docs/SEARXNG_DDG_INCIDENT_2026-09-08.md)
- [Đồng bộ `.env`](docs/ENV_SYNC.md)

---

## 1. Kiến trúc

```text
Telegram users
    │
    ▼
fcai-bot
    │  Docker network
    ├──────────────► fcai-searxng:8080 ──► upstream search engines
    │
    └──────────────► DDGS fallback (khi SEARCH_BACKEND=auto và policy cần)
```

- SearXNG phục vụ `web_search`/`image_search`.
- Direct URL reading là luồng riêng.
- Không có `ports:` cho service SearXNG.
- Không publish port giúp giảm exposure nhưng không phải authentication boundary; container khác trên cùng network vẫn có thể truy cập.
- Bot vẫn có per-user rate limit riêng qua `MAX_QUESTIONS_PER_MIN_PER_USER`.

---

## 2. File liên quan

- `docker-compose.yml` — profile `searxng`, Granian concurrency, mount config/cache.
- `searxng/settings.example.yml` — mẫu cấu hình; copy thành `settings.yml` khi cài mới.
- `searxng/limiter.toml` — file botdetection config rỗng hợp lệ được mount read-only.
- `.env.example` — `SEARCH_BACKEND`, `SEARXNG_URL`, timeout/circuit/cache settings và Granian options.
- `app/search/searxng_backend.py` — JSON API adapter.
- `app/search/router.py` — auto routing, top-up, deadline và fallback.
- `app/search/resilience.py` — circuit breaker, cache, singleflight.
- `app/search/runtime.py` — shared HTTP client và lifecycle.

`.env`, các backup `.env*.bak` và `searxng/settings.yml` chứa secret/production config và được ignore trong Git. Docker build context cũng loại env secret/backup files. Không commit/copy chúng vào artifact công khai.

---

## 3. Cài mới

`sync_env.py` có thể tự tạo `.env` từ `.env.example` với mode `0600`:

```bash
python scripts/sync_env.py

cp -i searxng/settings.example.yml searxng/settings.yml
openssl rand -hex 32
nano searxng/settings.yml   # thay secret_key
nano .env

docker compose --profile searxng config --quiet
docker compose --profile searxng up -d --build
```

Nếu đã tạo `.env` bằng cách khác, vẫn chạy `python scripts/sync_env.py` để đồng bộ key/comment/order và siết permission về owner-only.

Khuyến nghị production baseline:

```env
SEARCH_BACKEND=auto
SEARXNG_URL=http://searxng:8080
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
GRANIAN_BLOCKING_THREADS=1
GRANIAN_BACKPRESSURE=2
```

`SEARCH_BACKEND=auto` là recommended mode. `searxng` hoặc `ddgs` là strict single-backend modes và không fallback ngầm.

---

## 4. Upgrade deployment đã chạy

Sau `git pull`, backup rồi đồng bộ `.env` bằng script thay vì copy đè:

```bash
git pull
cp .env .env.bak
python scripts/sync_env.py
```

Script giữ value/secret hiện tại, thêm key mới, bỏ key đã bị loại khỏi `.env.example`, đồng bộ comment/order và siết `.env` về owner-only permissions. `.env.bak` được ignore nhưng vẫn chứa secret production; không upload/chia sẻ file này.

`searxng/settings.yml` là file thật ngoài Git nên **không tự cập nhật** khi `settings.example.yml` đổi. Nếu example có thay đổi cần áp dụng:

```bash
cp -p searxng/settings.yml searxng/settings.yml.bak
nano searxng/settings.yml
```

Giữ nguyên `secret_key` thật và merge thủ công các thay đổi cần thiết.

Sau khi sửa env/mount/image/config:

```bash
docker compose --profile searxng config --quiet
docker compose --profile searxng up -d --build --force-recreate bot searxng
```

Chỉ `docker compose restart` không áp dụng thay đổi image/mount/env mới.

---

## 5. SearXNG config hiện tại

`settings.yml` production nên giữ các invariant của file example:

```yaml
use_default_settings:
  engines:
    remove:
      - ahmia
      - torch
      - startpage
      - startpage news
      - startpage images
      - duckduckgo
      - wikipedia

server:
  secret_key: "<secret thật>"
  limiter: false
  public_instance: false

search:
  formats:
    - html
    - json
```

Lý do chính:

- `ahmia`, `torch`: cần Tor, không dùng trong deployment này.
- ba Startpage engines: workaround cho parser/response issue đã quan sát.
- `duckduckgo`: HTML engine có thể CAPTCHA trên datacenter egress.
- không enable `duckduckgo web` override theo incident hiện tại.
- `wikipedia`: workaround cho lỗi HTTP 400 đã quan sát.
- `json` bắt buộc cho bot JSON API.

Chi tiết DuckDuckGo: [docs/SEARXNG_DDG_INCIDENT_2026-09-08.md](docs/SEARXNG_DDG_INCIDENT_2026-09-08.md).

---

## 6. Granian và container policy

Compose hiện dùng:

```yaml
environment:
  GRANIAN_WORKERS: "1"
  GRANIAN_BLOCKING_THREADS: "${GRANIAN_BLOCKING_THREADS:-1}"
  GRANIAN_BACKPRESSURE: "${GRANIAN_BACKPRESSURE:-2}"
```

- 1 worker + 1 blocking thread phù hợp VPS nhỏ mặc định của repo.
- `backpressure=2` giới hạn request concurrent/worker.
- Không có `ports:`.
- `settings.yml` và `limiter.toml` mount read-only.
- Healthcheck dùng BusyBox-compatible `wget` gọi `/healthz` trong container.

Không dùng biến uWSGI để chỉnh Granian.

---

## 7. Search resilience của bot

Với `SEARCH_BACKEND=auto`:

```text
fresh cache
    ↓
SearXNG
    ↓ fail / web result quá ít
DDGS
    ↓ unavailable
stale cache
    ↓ miss
SearchError
```

Invariants:

- mỗi backend tối đa một attempt/request;
- không ping-pong SearXNG → DDGS → SearXNG;
- web: 2+ usable SearXNG results thì return; 1 result được giữ và DDGS top-up;
- image: 1 usable SearXNG result là đủ, không top-up;
- timeout backend bị clamp bởi `SEARCH_TOTAL_TIMEOUT_SEC` còn lại;
- web/image và từng backend có circuit riêng;
- 429 mở circuit ngay với cooldown dài hơn;
- fresh/stale cache chỉ lưu successful non-empty result;
- singleflight coalesce concurrent identical search;
- khi waiter cuối cùng bị cancel/rời flight, upstream task còn chạy bị cancel;
- caller cancellation không được tính thành backend failure;
- nếu HALF_OPEN probe bị caller cancel, probe slot được release và request sau có thể probe lại;
- shared SearXNG `httpx.AsyncClient` được reuse và đóng deterministic khi shutdown.

Chi tiết: [docs/SEARCH_RESILIENCE.md](docs/SEARCH_RESILIENCE.md).

---

## 8. Vận hành và smoke test

```bash
docker compose --profile searxng ps
docker compose logs --tail=100 searxng
docker compose logs --tail=100 bot

docker compose --profile searxng exec searxng \
  wget -qO- http://127.0.0.1:8080/healthz
# expected: OK
```

Test JSON API từ container bot để kiểm tra DNS/network bot → SearXNG:

```bash
docker compose --profile searxng exec -T bot python - <<'PY'
import httpx
from app.config import Settings

s = Settings()
url = s.searxng_url.strip().rstrip("/")
if not url:
    raise SystemExit("Thiếu SEARXNG_URL")

for category in (None, "images"):
    params = {"q": "Hà Nội", "format": "json"}
    if category:
        params["categories"] = category
    r = httpx.get(
        f"{url}/search",
        params=params,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    print("category:", category or "general", "HTTP:", r.status_code)
    r.raise_for_status()
    data = r.json()
    print("results:", len(data.get("results") or []))
    print("unresponsive_engines:", data.get("unresponsive_engines") or [])
PY
```

HTTP 200 + JSON hợp lệ + `results > 0` mới chứng minh query đó có usable result. `unresponsive_engines` có một vài engine nhưng vẫn có results là partial upstream failure, không đồng nghĩa toàn SearXNG request thất bại.

---

## 9. Direct X smoke test là luồng riêng

Không test direct-X bằng `/search?q=https://x.com/...`.

```bash
docker compose --profile searxng exec -T bot python - <<'PY'
import asyncio
from app.config import Settings
from app.search.url_service import read_url

URL = "https://x.com/TrustlessState/status/2097054140350554620"
r = asyncio.run(read_url(URL, Settings(), mode="auto"))
print("ok:", r.ok)
print("backend:", r.backend)
print("source:", r.source_url)
print("preview:", r.text[:300].replace("\n", " "))
PY
```

Nếu public status còn đọc được, normal success thường là `fxtwitter_status` hoặc `x_oembed`. Nếu specialized sources fail, runtime tiếp tục sang `crawl4ai` khi Crawl4AI active, rồi `generic_reader` nếu cần. Đây vẫn là URL-reading fallback, không liên quan SearXNG search.

---

## 10. Log/triệu chứng thường gặp

| Triệu chứng | Ý nghĩa / xử lý |
|---|---|
| `spawning up to 4 Python threads` | Dùng Granian defaults của Compose mới rồi recreate. |
| `missing config file: /etc/searxng/limiter.toml` | Mount `searxng/limiter.toml`; limiter vẫn có thể tắt. |
| `X-Forwarded-For nor X-Real-IP header is set!` | Có thể chấp nhận trong private direct-connect mode với limiter/public_instance tắt. Không giả IP ở bot. |
| Startpage JSON/parser error | Giữ ba Startpage engine trong removal list. |
| DuckDuckGo CAPTCHA / `duckduckgo web` issue | Giữ removal hiện tại; Python DDGS fallback là backend riêng. |
| Wikipedia HTTP 400 | Giữ workaround removal hiện tại. |
| `/search?format=json` 403 | Kiểm tra `search.formats` có `json`, rồi limiter/proxy. |
| `Connection refused` / DNS | Kiểm tra profile, health, Docker network, `SEARXNG_URL`. |
| Direct X URL bị search mirror/raw URL trước | Kiểm tra bot code mới, `X_FETCH_ENABLED=1`, `MAX_TOOL_ROUNDS=2`; direct URL phải đi `fetch_url` trước. |

---

## 11. Update policy

SearXNG là rolling release. Compose fallback mặc định hiện là `ghcr.io/searxng/searxng:latest`, nhưng repo cho phép override qua `SEARXNG_IMAGE`; production ổn định nên promote tag/digest đã test thay vì phụ thuộc mutable `latest` dài hạn.

Quy trình:

1. chọn candidate image;
2. validate `docker compose config` và YAML;
3. smoke-test JSON general + images;
4. chạy regression suite của bot;
5. candidate pass thì set/promote exact tag hoặc digest qua `SEARXNG_IMAGE`;
6. candidate fail thì giữ image production hiện tại.

Security fix hoặc engine fix liên quan production được ưu tiên test sớm.

---

## 12. Checklist production

- [ ] `searxng/settings.yml` là file thật, có random `secret_key`.
- [ ] `limiter: false`, `public_instance: false`, `search.formats` có `json`.
- [ ] Engine removals đã sync theo example hiện tại.
- [ ] Không override enable `duckduckgo web`.
- [ ] `searxng/limiter.toml` mount read-only.
- [ ] Không có host port mapping tới 8080.
- [ ] `.env` đã sync bằng `python scripts/sync_env.py` sau upgrade và không có group/other/execute bits.
- [ ] Backup `.env*.bak` không được commit/upload.
- [ ] `SEARCH_BACKEND=auto` và `SEARXNG_URL=http://searxng:8080` nếu dùng recommended mode.
- [ ] Search timeout/circuit/cache settings có giá trị hợp lệ.
- [ ] Production `SEARXNG_IMAGE` dùng tag/digest đã test nếu cần reproducible deploy.
- [ ] Container đã recreate sau khi sửa env/mount/image.
- [ ] `/healthz` trả `OK`.
- [ ] JSON general + images có usable results.
- [ ] Đã xem `unresponsive_engines` và bot logs.
- [ ] Đã smoke-test fallback/search trong Telegram.
- [ ] Direct X được smoke-test riêng bằng `read_url`, không dùng raw URL search.

---

## 13. Nếu sau này public SearXNG

Cấu hình repo hiện tại **không dành cho public mode**. Không chỉ thêm `ports:`: cần reverse proxy + HTTPS, trusted proxy/IP headers đúng, `server.limiter: true`, Valkey và policy JSON API phù hợp. Nên tách thành deployment design riêng thay vì sửa trực tiếp private profile này.
