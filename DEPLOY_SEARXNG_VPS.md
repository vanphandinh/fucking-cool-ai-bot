# SearXNG production trên VPS

Tài liệu này mô tả cấu hình **production baseline** cho SearXNG trong repo này.
Mục tiêu là chạy ổn định trên VPS, ít log lỗi vặt, an toàn hơn khi dùng cùng bot Telegram,
và vẫn mở đường để public qua domain riêng nếu bạn cần.

---

## 1. Kiến trúc khuyến nghị

### Mode khuyến nghị: private / internal

Đây là mode phù hợp nhất với repo này:

- `bot` gọi SearXNG qua Docker network: `http://searxng:8080`
- SearXNG có `valkey` riêng cho cache và sẵn sàng cho limiter
- host chỉ bind cổng SearXNG ra **loopback**: `127.0.0.1:18080`
- không mở trực tiếp SearXNG ra Internet
- nếu cần web UI thì đặt reverse proxy ở phía trước

Luồng gọi:

```text
Telegram users -> bot -> http://searxng:8080 -> upstream search engines
```

### Mode public qua domain riêng

Chỉ dùng khi bạn muốn cho browser/người dùng truy cập web UI SearXNG:

```text
Browser -> Nginx/Caddy/Traefik -> 127.0.0.1:18080 -> searxng -> upstream search engines
```

Ở mode này bạn nên:

- bật `SEARXNG_LIMITER=true`
- bật `SEARXNG_PUBLIC_INSTANCE=true`
- bật `SEARXNG_IMAGE_PROXY=true`
- đảm bảo reverse proxy truyền đúng `X-Forwarded-For` và `X-Real-IP`

---

## 2. Những file đã có sẵn trong repo

Repo đã có sẵn baseline để bạn không phải tự dựng lại từ đầu:

- `docker-compose.yml`
- `searxng/settings.example.yml`
- `searxng/limiter.example.toml`
- `.env.example`

Khi triển khai thật, bạn sẽ tạo thêm:

- `searxng/settings.yml`
- `searxng/limiter.toml`
- `.env`

> `searxng/settings.yml` và `searxng/limiter.toml` đã được ignore trong Git để tránh lộ secret và local overrides.

---

## 3. Chuẩn bị file cấu hình

Từ root repo:

```bash
cp .env.example .env
mkdir -p searxng
cp searxng/settings.example.yml searxng/settings.yml
cp searxng/limiter.example.toml searxng/limiter.toml
```

Sinh secret:

```bash
openssl rand -hex 32
```

Dùng chuỗi này cho `SEARXNG_SECRET` trong `.env` hoặc `secret_key` trong `searxng/settings.yml`.

---

## 4. Cấu hình `.env`

## 4.1. Mode private / internal

```env
SEARCH_BACKEND=searxng
SEARXNG_URL=http://searxng:8080
SEARXNG_SECRET=<chuỗi openssl rand -hex 32>

SEARXNG_BIND_IP=127.0.0.1
SEARXNG_HOST_PORT=18080

SEARXNG_LIMITER=false
SEARXNG_PUBLIC_INSTANCE=false
SEARXNG_IMAGE_PROXY=false
```

Giải thích:

- `SEARXNG_URL=http://searxng:8080`: bot gọi service nội bộ trong Docker
- `SEARXNG_BIND_IP=127.0.0.1`: host chỉ mở loopback, không public trực tiếp
- `SEARXNG_LIMITER=false`: phù hợp khi chỉ bot gọi nội bộ

## 4.2. Mode public qua domain

```env
SEARCH_BACKEND=searxng
SEARXNG_URL=http://searxng:8080
SEARXNG_SECRET=<chuỗi openssl rand -hex 32>

SEARXNG_BASE_URL=https://search.example.com/
SEARXNG_BIND_IP=127.0.0.1
SEARXNG_HOST_PORT=18080

SEARXNG_LIMITER=true
SEARXNG_PUBLIC_INSTANCE=true
SEARXNG_IMAGE_PROXY=true
```

Gợi ý thêm:

```env
# nên pin image cụ thể khi production nếu muốn giảm rủi ro đổi behavior từ latest
SEARXNG_IMAGE=ghcr.io/searxng/searxng:latest
SEARXNG_VALKEY_IMAGE=docker.io/valkey/valkey:9-alpine
```

---

## 5. Cấu hình `searxng/settings.yml`

File mẫu trong repo đã được tinh chỉnh cho VPS:

- bật `formats: [html, json]` để bot/API dùng được JSON
- thêm `valkey.url`
- thêm security headers cơ bản
- tắt sẵn các engine hay gây lỗi hoặc noise log trên VPS/datacenter IP:
  - `ahmia`
  - `torch`
  - `startpage`

Trích đoạn quan trọng:

```yaml
use_default_settings: true

server:
  secret_key: "REPLACE_WITH_A_RANDOM_HEX_STRING_AT_LEAST_32_CHARS"
  limiter: false
  public_instance: false
  image_proxy: false

valkey:
  url: "valkey://searxng-valkey:6379/0"

search:
  autocomplete: "duckduckgo"
  formats:
    - html
    - json
```

### Vì sao tắt `startpage`, `ahmia`, `torch`

Trên VPS IP datacenter, các engine này thường gây:

- lỗi load engine khi startup
- anti-bot / CAPTCHA
- log warning lặp lại nhưng không giúp tăng chất lượng tìm kiếm

Bạn vẫn còn các engine mặc định khác từ SearXNG do `use_default_settings: true`.

---

## 6. Cấu hình `searxng/limiter.toml`

File này dùng cho botdetection / limiter.

### Khi chỉ dùng nội bộ cho bot

Giữ nguyên mức tối thiểu như file mẫu. Bạn có thể để:

- `link_token = false`
- `trusted_proxies` chỉ gồm loopback

### Khi public qua reverse proxy

- bật `SEARXNG_LIMITER=true`
- thêm IP/CIDR của reverse proxy vào `trusted_proxies`
- nếu proxy chạy trong Docker bridge riêng, thêm CIDR bridge đó

Ví dụ:

```toml
[botdetection]
trusted_proxies = [
  '127.0.0.0/8',
  '::1',
  '172.16.0.0/12',
]

[botdetection.ip_limit]
link_token = true
```

> Nếu không cấu hình proxy headers đúng, limiter có thể hiểu mọi request đều đến từ proxy và rate-limit sai.

---

## 7. Chạy stack

```bash
docker compose --profile searxng up -d --build
```

Kiểm tra:

```bash
docker compose ps
docker compose logs --tail=100 searxng
docker compose logs --tail=100 searxng-valkey
curl -fsS http://127.0.0.1:18080/healthz
```

Nếu bot dùng SearXNG, kiểm tra thêm log bot:

```bash
docker compose logs --tail=100 bot
```

---

## 8. Reverse proxy mẫu với Nginx

Nếu bạn muốn mở giao diện ra domain riêng:

```nginx
server {
    listen 80;
    server_name search.example.com;

    location / {
        proxy_pass http://127.0.0.1:18080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Khuyến nghị production thực tế:

- thêm HTTPS bằng Let's Encrypt
- redirect HTTP -> HTTPS
- chỉ public Nginx/Caddy/Traefik, **không public trực tiếp Valkey**
- không đổi `SEARXNG_URL` của bot; bot vẫn nên gọi nội bộ `http://searxng:8080`

---

## 9. Những lỗi thường gặp

### `missing config file: /etc/searxng/limiter.toml`

Nguyên nhân:
- bạn chưa copy `searxng/limiter.example.toml` thành `searxng/limiter.toml`

Cách xử lý:

```bash
cp searxng/limiter.example.toml searxng/limiter.toml
docker compose --profile searxng up -d
```

### `X-Forwarded-For nor X-Real-IP header is set!`

Nguyên nhân thường gặp:
- đang public qua reverse proxy nhưng chưa forward headers đúng

Cách xử lý:
- thêm `proxy_set_header X-Real-IP $remote_addr;`
- thêm `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`
- rà lại `trusted_proxies` trong `limiter.toml`

### `startpage ... JSONDecodeError`

Nguyên nhân:
- upstream Startpage trả trang anti-bot / abuse page thay vì dữ liệu mà engine mong đợi

Trong baseline của repo, `startpage` đã bị disable sẵn.

### `ahmia` / `torch` can't register engine

Đây là các engine dễ fail lúc startup trên một số image / môi trường. Baseline của repo đã disable sẵn.

### `wikipedia ... 400`

Repo đã bỏ hard-code `language=vi` trong backend gọi SearXNG để giảm lỗi ép query sang `vi.wikipedia.org`.

---

## 10. Cập nhật và backup

### Cập nhật code repo

```bash
git pull
docker compose --profile searxng up -d --build
```

### Cập nhật image

Nếu bạn dùng tag pinned, đổi tag rồi chạy:

```bash
docker compose --profile searxng pull
docker compose --profile searxng up -d
```

### Backup tối thiểu

Nên backup:

- `.env`
- `searxng/settings.yml`
- `searxng/limiter.toml`

Thường **không cần** backup cache SearXNG hay dữ liệu Valkey cho use case của repo này.

---

## 11. Checklist production ngắn

Trước khi coi là xong, kiểm tra các mục sau:

- [ ] `SEARXNG_SECRET` đã đổi khỏi giá trị mặc định
- [ ] `searxng/settings.yml` và `searxng/limiter.toml` đã được tạo từ file mẫu
- [ ] bot dùng `SEARXNG_URL=http://searxng:8080`
- [ ] host chỉ bind SearXNG ra `127.0.0.1`
- [ ] Valkey không public ra Internet
- [ ] nếu có reverse proxy: đã forward `X-Real-IP` và `X-Forwarded-For`
- [ ] nếu public thật sự: đã bật `SEARXNG_LIMITER=true`
- [ ] `curl http://127.0.0.1:18080/healthz` trả OK
- [ ] `docker compose logs --tail=100 searxng` không còn warning tránh được như thiếu `limiter.toml`

---

## 12. Kết luận

Với repo này, cấu hình production hợp lý nhất trên VPS là:

1. giữ SearXNG ở **private/internal mode**
2. cho bot gọi nội bộ qua Docker network
3. chỉ mở ra domain riêng khi thật sự cần
4. nếu public thì dùng reverse proxy + limiter + trusted proxy headers đúng chuẩn

Nếu bạn muốn, bước tiếp theo nên làm là tạo thêm:

- cấu hình **Nginx HTTPS hoàn chỉnh**
- hoặc cấu hình **Caddy / Traefik** tương đương
