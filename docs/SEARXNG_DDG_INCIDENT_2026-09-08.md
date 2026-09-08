# SearXNG DuckDuckGo incident — 2026-09-08

Hai engine DuckDuckGo của SearXNG hiện không phù hợp để bật trên deployment production của repo này:

- `duckduckgo` (HTML engine) có thể trả `SearxEngineCaptchaException` trên datacenter egress.
- `duckduckgo web` hiện có upstream bot-detection issue sau migration `curl_cffi`; response có thể là JSON không có `results`, làm engine ném `KeyError` tại `duckduckgo_web.py`.

Repo vì vậy tạm thời remove exact engine name `duckduckgo` trong `searxng/settings.example.yml` và **không** enable `duckduckgo web`. `SEARCH_BACKEND=auto` của bot vẫn ưu tiên SearXNG; nếu SearXNG không có usable result hoặc request backend lỗi, bot có thể fallback sang Python `ddgs` backend như trước.

> Incident này áp dụng cho **web/image search qua SearXNG**. Direct X/Twitter status URL là luồng riêng:
> `fetch_url` ưu tiên FxTwitter API v2 → X oEmbed → generic reader khi `X_FETCH_ENABLED=1`.
> Không dùng raw X URL search qua SearXNG làm smoke test cho direct-X resolver. Xem
> [X_CONTENT_FETCHING.md](X_CONTENT_FETCHING.md).

## Áp dụng trên VPS đang chạy

`searxng/settings.yml` là file thật được bind-mount và nằm ngoài Git. Cập nhật file mẫu trong repo **không tự cập nhật** file thật.

Trong `searxng/settings.yml`, merge `duckduckgo` vào đúng block removal hiện tại và giữ nguyên `secret_key`:

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
```

Không thêm block sau:

```yaml
engines:
  - name: duckduckgo web
    disabled: false
```

Sau khi sửa file thật:

```bash
docker compose --profile searxng config --quiet
docker compose --profile searxng up -d --force-recreate searxng
docker compose logs --since=5m searxng
```

Nếu bot cũng vừa được cập nhật code trong cùng lần deploy:

```bash
docker compose --profile searxng up -d --build --force-recreate bot searxng
```

Nếu deployment có PR direct-X mới, đồng thời kiểm tra `.env`:

```env
X_FETCH_ENABLED=1
MAX_TOOL_ROUNDS=2
```

Không tăng `MAX_TOOL_ROUNDS` để bù cho search/fetch failure.

## Kiểm tra SearXNG

Từ container bot:

```bash
docker compose --profile searxng exec -T bot python - <<'PY'
import httpx
from app.config import Settings

url = Settings().searxng_url.strip().rstrip('/')
response = httpx.get(
    f"{url}/search",
    params={"q": "Việt Nam đất hiếm", "format": "json"},
    timeout=30,
)
response.raise_for_status()
data = response.json()
print("results:", len(data.get("results") or []))
print("unresponsive_engines:", data.get("unresponsive_engines") or [])
PY
```

Không nên còn thấy warning từ exact engine `duckduckgo` hoặc `duckduckgo web` nếu file thật không có override enable riêng. Engine khác vẫn có thể xuất hiện trong `unresponsive_engines`; đó là partial upstream failure và không đồng nghĩa toàn bộ SearXNG request thất bại.

## Kiểm tra direct X/Twitter riêng

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
PY
```

Nếu post vẫn public, normal success thường là `fxtwitter_status` hoặc `x_oembed`. Đây là test khác với SearXNG JSON search ở trên.

## Khi nào bật lại

Chỉ bật lại DuckDuckGo engine trong SearXNG sau khi upstream fix đã merge vào image/tag mà repo đang pin và A/B test live trên VPS xác nhận query general trả `results` ổn định mà không CAPTCHA/challenge. Không dùng global browser-impersonation workaround chỉ để chữa riêng DDG vì có thể làm hỏng các engine khác.