# SearXNG production trên VPS — private, chỉ cho bot Telegram

Instance trong repo này là **PRIVATE**: bot gọi JSON API qua Docker network,
**không publish cổng ra host/Internet**. Không cần reverse proxy, domain hay
Valkey cho cấu hình này; `server.limiter` và `server.public_instance` được tắt
rõ ràng. Tuy vậy, SearXNG vẫn khởi tạo **botdetection** và đọc `limiter.toml`
kể cả khi limiter tắt — hai việc này không đồng nghĩa với nhau.

> Direct X/Twitter status URL **không đi qua SearXNG khi direct fetch thành công**.
> `fetch_url` ưu tiên FxTwitter API v2 → X oEmbed → generic reader. Xem
> [docs/X_CONTENT_FETCHING.md](docs/X_CONTENT_FETCHING.md). Đừng dùng lỗi direct-X làm lý do
> tăng `MAX_TOOL_ROUNDS` hoặc thay đổi engine SearXNG nếu resolver chuyên biệt vẫn hoạt động.

---

## 1. Kiến trúc

```text
Telegram users  ──►  bot container  ──►  http://searxng:8080  ──►  upstream search engines
                     (fcai-bot)         (fcai-searxng, Docker network, KHÔNG publish port)
```

- Bot gọi SearXNG trực tiếp, **không có reverse proxy** ở giữa.
- Không publish port giúp tránh mở dịch vụ ra Internet; đây **không phải cơ chế
  xác thực**. Host và container khác được gắn vào cùng network vẫn có thể truy cập.
- Limiter của SearXNG tắt, không cần Valkey cho rate limit. Bot vẫn có giới hạn
  số câu hỏi theo user (`MAX_QUESTIONS_PER_MIN_PER_USER`).
- Gỡ lỗi bằng `docker compose exec` (mục 5), không cần mở port.
- SearXNG phục vụ `web_search`/`image_search`; direct URL reading là luồng riêng.

---

## 2. Các file liên quan

- `docker-compose.yml` — profile `searxng`, giới hạn concurrency Granian, mount config/cache
- `searxng/settings.example.yml` — mẫu cấu hình, copy thành file thật khi cài mới
- `searxng/limiter.toml` — file có sẵn trong Git, chỉ có comment, kế thừa mặc định botdetection
- `.env.example` — backend, URL, image và tùy chọn Granian
- `app/search/searxng_backend.py` — gọi JSON API; log `unresponsive_engines` nếu có,
  vẫn giữ kết quả từ các engine hoạt động
- `docs/SEARXNG_DDG_INCIDENT_2026-09-08.md` — lý do tạm loại DuckDuckGo engine khỏi SearXNG
- `docs/X_CONTENT_FETCHING.md` — direct-X resolver độc lập với SearXNG

Khi triển khai thật, tạo thêm `.env` và `searxng/settings.yml` (chứa `secret_key`).
Cả hai đã được ignore trong Git. **Không commit hoặc gửi nội dung secret vào chat.**

---

## 3. Triển khai và áp dụng bản sửa

### 3.1. Cài mới

Từ root repo:

```bash
# Chỉ copy khi CHƯA có settings.yml
cp -i searxng/settings.example.yml searxng/settings.yml
openssl rand -hex 32
nano searxng/settings.yml          # thay secret_key bằng kết quả lệnh trên

# Khuyến nghị: auto ưu tiên SearXNG rồi fallback DDGS cho cả web + image search.
# Có thể dùng SEARCH_BACKEND=searxng nếu muốn khóa cứng vào SearXNG.
# SEARXNG_URL=http://searxng:8080
# Nếu chưa có .env: cp -i .env.example .env, rồi điền token/key như README
nano .env

docker compose --profile searxng config --quiet
docker compose --profile searxng up -d --build
```

`GRANIAN_BLOCKING_THREADS=1` và `GRANIAN_BACKPRESSURE=2` là mặc định trong
Compose; không bắt buộc thêm chúng vào `.env` cũ. `SEARCH_BACKEND` hiện hỗ trợ
`auto`, `searxng`, `ddgs`; `auto` là mặc định và ưu tiên SearXNG khi có URL.

### 3.2. Đã chạy bản cũ (các log ngày 2026-09-07/08)

**Sửa file mẫu không tự cập nhật file thật** `searxng/settings.yml` vì file thật
được ignore trong Git và đang bind-mount vào container.

1. Cập nhật code repo, trong đó phải có `searxng/limiter.toml` và mount hiện tại trong Compose.
2. Backup rồi mở **file thật**:

   ```bash
   cp -p searxng/settings.yml searxng/settings.yml.bak
   nano searxng/settings.yml
   ```

   Backup `settings.yml.*` cũng được ignore vì có secret. Merge theo mẫu ở mục 4:
   - Thay `use_default_settings: true` bằng block `use_default_settings.engines.remove`.
   - Trong block `server` hiện có, đặt `limiter: false`, `public_instance: false`.
   - Giữ `search.formats` có `json`.
   - **Giữ nguyên `secret_key` thật và các tùy chỉnh cần thiết**. Không chép
     placeholder từ mẫu, không thêm trùng các key YAML `server`/`search`.
   - Nếu có override engine cũ ở `engines:`, bỏ các entry `ahmia`, `torch`,
     `startpage`, `startpage news`, `startpage images`, `duckduckgo`, `wikipedia` khỏi đó:
     override có thể thêm lại engine vừa `remove`.
   - Không enable override `duckduckgo web`; xem incident note để biết lý do.
   - Nếu service `searxng` còn env `SEARXNG_LIMITER`/`SEARXNG_PUBLIC_INSTANCE`
     từ cấu hình riêng, bỏ chúng hoặc đặt `false`; env có thể ghi đè YAML.
3. Recreate để áp dụng cả mount/env mới, đồng thời build bot có log chẩn đoán:

   ```bash
   docker compose --profile searxng config --quiet
   docker compose --profile searxng up -d --build --force-recreate bot searxng
   docker compose logs --since=5m searxng bot
   ```

Chỉ `docker compose restart` **không** áp dụng thay đổi mount/env/image trong
Compose. Không cần `down -v` hay xóa cache để xử lý những log này.

---

## 4. Giải thích cấu hình

### 4.1. Granian và Compose

Image hiện tại khởi động bằng **Granian**, không phải uWSGI. Compose truyền:

```yaml
environment:
  GRANIAN_WORKERS: "1"
  GRANIAN_BLOCKING_THREADS: "${GRANIAN_BLOCKING_THREADS:-1}"
  GRANIAN_BACKPRESSURE: "${GRANIAN_BACKPRESSURE:-2}"
```

- Giữ một worker; mặc định một Python blocking thread phù hợp VPS nhỏ, thay vì
  bốn thread mặc định của image đang gây warning.
- `backpressure` giới hạn số request đồng thời **mỗi worker**. Tăng thread /
  backpressure chỉ sau khi đo CPU/RAM/độ trễ; tăng quá cao có thể tái xuất hiện warning.
- Đây không phải giới hạn CPU/RAM cứng hay giới hạn số engine trong một truy vấn.
  SearXNG vẫn có thể gọi nhiều engine cho cùng truy vấn.
- Các biến `UWSGI_THREADS`/`UWSGI_WORKERS` không cấu hình Granian.
- Mount read-only cả `settings.yml` lẫn `limiter.toml`, cộng volume `searxng-cache`.
  Không có `ports:`, không thêm Valkey/reverse proxy.
- Healthcheck dùng BusyBox-compatible `wget` gọi `/healthz` trong container.

### 4.2. `searxng/settings.yml`

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
  secret_key: "<secret hiện tại, hoặc openssl rand -hex 32 khi cài mới>"
  limiter: false
  public_instance: false

search:
  formats:
    - html
    - json
```

- Vẫn kế thừa mặc định upstream, chỉ **loại bảy engine theo tên**. `remove`
  so khớp chính xác `engine.name`; nó không loại mọi engine cùng dùng một module.
- `ahmia`, `torch`: engine onion cần Tor, không dùng trong kiến trúc này.
- `startpage`, `startpage news`, `startpage images`: workaround tạm thời cho lỗi parser JSON từ Startpage;
  phải remove đủ ba vì chúng là ba engine name riêng.
- `duckduckgo`: HTML engine thường gặp CAPTCHA trên datacenter egress. `duckduckgo web` cũng đang có upstream
  bot-detection/response-shape issue nên **không enable override**. Xem
  [docs/SEARXNG_DDG_INCIDENT_2026-09-08.md](docs/SEARXNG_DDG_INCIDENT_2026-09-08.md).
- `wikipedia`: workaround tạm thời cho HTTP 400 đã quan sát; engine khác vẫn có thể trả Wikipedia link.
- `search.formats` phải có `json`, nếu không `/search?format=json` có thể bị 403.
- Không thêm `SEARXNG_SECRET` ở service searxng: secret nằm duy nhất trong file thật.

### 4.3. `searxng/limiter.toml` và header IP

File TOML chỉ có comment là cấu hình rỗng hợp lệ. SearXNG giữ schema/mặc định upstream,
nhưng không còn warning **missing config file**. Mount file này **không bật limiter**;
việc bật/tắt nằm ở `server.limiter`/`public_instance`.

Middleware xác định IP vẫn có thể ghi:

```text
X-Forwarded-For nor X-Real-IP header is set!
```

Với bot gọi **trực tiếp** và `limiter: false`/`public_instance: false`, đây là
log từ giả định có reverse proxy, không phải bằng chứng API bị từ chối. Khi
không có các header đó, middleware dùng `REMOTE_ADDR` của kết nối thật; thông
báo được ghi một lần trong mỗi worker. Không cần thêm reverse proxy chỉ để xóa log.

**Không gắn IP giả `127.0.0.1` vào request bot**, không trust toàn bộ Internet và
không tắt logger lỗi chung để che dòng này. Telegram không cung cấp IP người dùng
cho bot. Nếu triển khai public/proxy thì phải chuyển sang cấu hình mục 9, proxy
phải ghi đè header bằng IP thực và chỉ trust đúng proxy đó.

---

## 5. Vận hành & kiểm tra

```bash
docker compose --profile searxng ps
docker compose logs --tail=100 searxng
docker compose logs --tail=100 bot

# Kiểm tra web server sống (KHÔNG chứng minh upstream search hoạt động):
docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/healthz
# Kỳ vọng: OK
```

Test JSON API **từ container bot**, dùng đúng `SEARXNG_URL` cấu hình, để kiểm tra
cả DNS/network bot → SearXNG:

```bash
docker compose --profile searxng exec -T bot python - <<'PY'
import httpx
from app.config import Settings

url = Settings().searxng_url.strip().rstrip("/")
if not url:
    raise SystemExit("Thiếu SEARXNG_URL trong container bot")
for category in (None, "images"):
    params = {"q": "Hà Nội", "format": "json"}
    if category:
        params["categories"] = category
    response = httpx.get(
        f"{url}/search",
        params=params,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    print("category:", category or "general", "HTTP:", response.status_code)
    response.raise_for_status()
    data = response.json()
    print("results:", len(data.get("results") or []))
    print("unresponsive_engines:", data.get("unresponsive_engines") or [])
PY
```

- HTTP 200, JSON hợp lệ, `results > 0`: có kết quả tìm kiếm.
- Kiểm tra cả `general` và `images`; không nên còn thấy exact engine `duckduckgo` hoặc ba Startpage engine
  nếu file thật đã sync đúng và không có override riêng.
- `unresponsive_engines` có engine khác nhưng vẫn có `results`: lỗi **một phần**;
  bot giữ kết quả tốt và log metadata engine lỗi, không coi cả request thất bại.
- Không có kết quả: thử một query phổ biến khác và xem metadata/log engine.
- 403/429, JSON không hợp lệ, lỗi kết nối hoặc timeout: kiểm tra config/limiter/network.

### 5.1. Direct X/Twitter smoke test là luồng riêng

Không dùng `/search?q=https://x.com/...` để test khả năng đọc một X status URL. Test direct resolver:

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

Nếu post vẫn public, backend thường là `fxtwitter_status` hoặc `x_oembed`; source vẫn phải là canonical X URL.
Nếu specialized upstream fail, generic reader mới là fallback cuối. Chỉ sau direct fetch không đủ dữ liệu thì
AI mới nên cân nhắc `web_search`.

Bot tìm kiếm thành công khi câu trả lời sử dụng được kết quả web/ảnh phù hợp;
việc chỉ không thấy warning backend chưa đủ để xác nhận.

---

## 6. Xử lý các log thường gặp

| Log / triệu chứng | Ý nghĩa và cách xử lý |
|---|---|
| Granian cảnh báo `spawning up to 4 Python threads` | Cảnh báo hiệu năng, không phải crash. Dùng `GRANIAN_BLOCKING_THREADS=1`, `GRANIAN_BACKPRESSURE=2` như Compose mới rồi recreate. |
| `Listening at: http://:::8080`, `Started worker-1` | Worker đã khởi động. `::` là địa chỉ bind IPv6 trong container, không tự publish cổng ra host. |
| `ahmia` / `torch`: `can't register engine (loading engine failed)` | Với cấu hình mặc định không có Tor, loại chúng bằng `use_default_settings.engines.remove`. Nếu vẫn xảy ra, kiểm tra file thật và override `engines:`. |
| `missing config file: /etc/searxng/limiter.toml` | Botdetection vẫn đọc file dù limiter tắt. Mount `searxng/limiter.toml` rồi recreate. Không cần bật limiter/Valkey. |
| `X-Forwarded-For nor X-Real-IP header is set!` | Có thể chấp nhận ở mô hình private gọi trực tiếp, limiter/public_instance đều tắt. Không giả IP ở bot. |
| `startpage images` + `JSONDecodeError: Extra data` | Startpage response không khớp parser hiện tại. `409` trong `ErrorContext(..., 409, ...)` là số dòng Python, không phải HTTP 409. Remove đủ `startpage`, `startpage news`, `startpage images`. |
| `duckduckgo` CAPTCHA / `duckduckgo web` bot-detection hoặc `KeyError` | Giữ exact `duckduckgo` trong removal list và không enable `duckduckgo web`. Python DDGS fallback là backend riêng, không đồng nghĩa bật DuckDuckGo engine trong SearXNG. |
| Wikipedia `HTTPError ... 400 ... vi.wikipedia.org` | Tạm remove `wikipedia`; không đủ căn cứ đổi toàn bộ bot sang tiếng Anh. |
| `/etc/searxng/settings.yml is not a valid file` | Chưa tạo file thật hoặc Docker tạo directory ở bind-mount path. Kiểm tra `test -f searxng/settings.yml`. |
| `/search?format=json` bị 403 | Kiểm tra `search.formats` có `json`; nếu có, xem thêm limiter/proxy. |
| Bot báo `Connection refused` / lỗi DNS | Kiểm tra profile, container health, Docker network và `SEARXNG_URL=http://searxng:8080`. |
| CAPTCHA / block IP datacenter / lỗi một vài engine | Với `SEARCH_BACKEND=auto`, SearXNG lỗi/rỗng có thể fallback DDGS; `searxng` thì khóa cứng. |
| User gửi direct X URL nhưng log lại search raw URL/mirror | Đây không phải lỗi cần chỉnh engine SearXNG. Kiểm tra bot đã deploy code direct-X, `X_FETCH_ENABLED=1`, `MAX_TOOL_ROUNDS=2`; direct URL phải đi `fetch_url` trước search. |

`SEARCH_BACKEND=auto` dùng cùng policy cho web + image search: ưu tiên SearXNG
khi `SEARXNG_URL` có cấu hình, sau đó fallback DDGS nếu SearXNG lỗi hoặc không
có kết quả usable. Fallback AI provider và direct URL reader là các luồng riêng.

Đối chiếu upstream:
[Granian](https://docs.searxng.org/admin/installation-granian.html),
[settings loader](https://github.com/searxng/searxng/blob/master/searx/settings_loader.py),
[limiter.initialize](https://github.com/searxng/searxng/blob/master/searx/limiter.py),
[ProxyFix](https://github.com/searxng/searxng/blob/master/searx/botdetection/trusted_proxies.py).

---

## 7. Cập nhật & backup

- Cập nhật code, merge thay đổi mẫu vào `settings.yml` thật (mục 3.2) rồi chạy lại
  `docker compose --profile searxng up -d --build`.
- Cập nhật image: đặt `SEARXNG_IMAGE` trong `.env` thành **tag hoặc digest đã kiểm tra**, rồi chạy:

  ```bash
  docker compose --profile searxng pull searxng
  docker compose --profile searxng up -d searxng
  ```

  Kiểm tra lại JSON API sau mỗi lần nâng cấp. `latest` thay đổi theo thời gian;
  không đảm bảo mọi engine đều hoạt động trên mọi IP VPS.
- Backup tối thiểu `.env` + `searxng/settings.yml` và override riêng nếu có.
  `limiter.toml` mặc định đã ở Git; cache SearXNG không cần backup.

---

## 8. Checklist production

- [ ] File thật `searxng/settings.yml` có secret ngẫu nhiên, không phải placeholder
- [ ] File thật có `limiter: false`, `public_instance: false`, format `json`
- [ ] Engine removals đã sync đủ: `ahmia`, `torch`, `startpage`, `startpage news`, `startpage images`, `duckduckgo`, `wikipedia`
- [ ] Không có override enable `duckduckgo web`
- [ ] `searxng/limiter.toml` được mount read-only, không còn warning thiếu file
- [ ] `.env` có `SEARXNG_URL=http://searxng:8080`; `SEARCH_BACKEND=auto` (khuyến nghị) hoặc `searxng` nếu muốn khóa cứng
- [ ] Nếu dùng direct-X: `X_FETCH_ENABLED=1`, `MAX_TOOL_ROUNDS=2`
- [ ] Container đã recreate sau khi sửa mount/env trong Compose
- [ ] Không có port mapping `HOST:PORT->8080/tcp` (`8080/tcp` đơn thuần là expose, không phải publish)
- [ ] `/healthz` → `OK`; test JSON từ container bot có kết quả general + images
- [ ] Đã xem `unresponsive_engines`, log bot và thử câu hỏi cần tìm web/ảnh trong Telegram
- [ ] Đã smoke-test direct X URL riêng, không dùng raw-URL search làm test resolver

---

## 9. Nếu sau này muốn public SearXNG

Cấu hình repo **không dành cho mode public**. Đừng chỉ thêm `ports:` hoặc bật
limiter đơn lẻ: cần reverse proxy + HTTPS, header IP thật, `trusted_proxies` chỉ
chứa proxy tin cậy, `server.limiter: true` + Valkey và cấu hình JSON API phù hợp.
Tham khảo tài liệu limiter chính thức [2](https://docs.searxng.org/admin/searx.limiter.html)
và [hướng dẫn container](https://docs.searxng.org/admin/installation-docker.html)
để dựng riêng một triển khai public.