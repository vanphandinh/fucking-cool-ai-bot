# SearXNG production trên VPS — private, chỉ cho bot Telegram

Tài liệu này mô tả cách chạy SearXNG **production** cho repo này: instance
**PRIVATE**, chỉ phục vụ bot Telegram, **không public ra ngoài**. Mọi setting
phục vụ mode public (limiter, Valkey, reverse proxy, trusted proxies, port
publish, base_url, image_proxy…) đã được **lược bỏ** khỏi cấu hình để giữ
một nguồn cấu hình duy nhất, dễ vận hành và không mở thêm bề mặt tấn công.

---

## 1. Kiến trúc

```text
Telegram users  ──►  bot container  ──►  http://searxng:8080  ──►  upstream search engines
                     (fcai-bot)         (fcai-searxng, Docker network, KHÔNG publish port)
```

- Bot gọi SearXNG qua **Docker network nội bộ** (`http://searxng:8080`).
- Container SearXNG **không publish cổng ra host / Internet** → không ai ngoài
  Docker network (tức ngoài bot) truy cập được: không cần limiter, không cần
  reverse proxy, không cần domain, không cần Valkey.
- Nếu muốn gỡ lỗi, dùng `docker compose exec` ở ngay trong container (mục 5),
  không mở port.

> Vì sao không cần Valkey: Valkey trong SearXNG chỉ phục vụ limiter /
> botdetection khi instance **public** (`server.limiter: true`). Instance private
> với `limiter: false` (mặc định) chạy tốt không cần Valkey.

---

## 2. Các file liên quan trong repo

- `docker-compose.yml` — service `searxng` (profile `searxng`) + volume cache
- `searxng/settings.example.yml` — mẫu cấu hình, copy thành file thật
- `.env.example` — `SEARCH_BACKEND`, `SEARXNG_URL`, `SEARXNG_IMAGE`

Khi triển khai thật bạn tạo thêm:

- `searxng/settings.yml` — cấu hình thật (chứa `secret_key`, đã ignore trong Git)
- `.env` — từ `.env.example`

> Không còn file `limiter.toml`: instance private không bật limiter nên không
> cần mount cấu hình botdetection.

---

## 3. Các bước triển khai

Từ root repo:

```bash
# 1) Tạo settings.yml từ mẫu và đổi secret_key
cp searxng/settings.example.yml searxng/settings.yml
nano searxng/settings.yml          # secret_key: "<kết quả lệnh dưới>"
openssl rand -hex 32

# 2) .env: chuyển bot sang dùng SearXNG
#    SEARCH_BACKEND=searxng
#    SEARXNG_URL=http://searxng:8080
nano .env

# 3) Chạy (build bot + bật profile searxng)
docker compose --profile searxng up -d --build
```

Nếu chỉ muốn chạy bot (không dùng SearXNG) thì giữ `SEARCH_BACKEND=ddgs` và bỏ
`--profile searxng` như bình thường:

```bash
docker compose up -d --build
```

---

## 4. Giải thích cấu hình tối giản

### 4.1. `docker-compose.yml` (service `searxng`)

- **Không có `ports:`** → không mở cổng ra host. Đây là điểm mấu chốt giữ cho
  instance private.
- Chỉ mount `./searxng/settings.yml` (read-only) và volume `searxng-cache`
  (cache nội bộ container, không quan trọng để backup).
- `SEARXNG_SECRET` **không** đặt trong `.env`/compose nữa — secret nằm duy nhất
  trong `searxng/settings.yml` (một nguồn, không lệch nhau).
- Các biến `SEARXNG_BASE_URL` / `SEARXNG_BIND_IP` / `SEARXNG_HOST_PORT` /
  `SEARXNG_LIMITER` / `SEARXNG_PUBLIC_INSTANCE` / `SEARXNG_IMAGE_PROXY` /
  `SEARXNG_VALKEY_URL` và service `searxng-valkey` đã bị xóa — chúng chỉ phục vụ
  mode public/reverse-proxy.
- `healthcheck` gọi `/healthz` ngay trong container.

### 4.2. `searxng/settings.yml`

```yaml
use_default_settings: true

server:
  secret_key: "<openssl rand -hex 32>"

search:
  formats:
    - html
    - json
```

- `use_default_settings: true` → dùng toàn bộ mặc định upstream (gồm cả danh
  sách engine được upstream bảo trì theo từng bản). Không tự disable engine —
  tránh danh sách cũ gây lỗi khi nâng cấp.
- `search.formats: [html, json]` → **bắt buộc** có `json` để bot gọi JSON API
  (`format=json`); mặc định upstream chỉ bật `html`.
- Mọi thứ khác (limiter, public_instance, image_proxy, valkey, ui, security
  headers…) giữ mặc định an toàn của SearXNG: `limiter: false`,
  `public_instance: false`, không valkey — đủ và đúng cho instance private.

---

## 5. Vận hành & kiểm tra

```bash
docker compose --profile searxng ps
docker compose logs --tail=100 searxng          # log SearXNG
docker compose logs --tail=100 bot              # log bot (tìm dòng search OK/ERR)

# Healthcheck nội bộ (không cần mở port):
docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/healthz
# kỳ vọng: OK

# Test đúng đường bot đi — JSON API qua Docker network:
docker compose --profile searxng exec searxng wget -qO- 'http://127.0.0.1:8080/search?q=test&format=json' | head -c 500
```

Bot dùng SearXNG thành công khi log bot không còn báo
`Search backend 'searxng' lỗi` và câu trả lời có kèm 📚 nguồn tham khảo.

---

## 6. Xử lý sự cố thường gặp

| Triệu chứng | Nguyên nhân & cách xử lý |
|---|---|
| `docker compose --profile searxng up` dừng ngay, log SearXNG báo `/etc/searxng/settings.yml is not a valid file` | Chưa tạo `searxng/settings.yml` (chưa copy từ `settings.example.yml`) |
| Log SearXNG cảnh báo `missing config file: /etc/searxng/limiter.toml` | Vô hại: instance private không bật limiter, không cần file này — bỏ qua |
| Gọi `format=json` bị lỗi 4xx | Thiếu `search.formats: [html, json]` trong `searxng/settings.yml` (mẫu mới đã có sẵn) |
| Đang nâng cấp từ cấu hình cũ (có service `searxng-valkey`, `valkey.url`, env `SEARXNG_*`) | Copy lại `searxng/settings.yml` từ mẫu mới, hoặc xóa các block `valkey:`/limiter — repo không còn service `searxng-valkey`; các biến `SEARXNG_*` cũ trong `.env` giờ vô dụng (có thể xóa) |
| Bot báo `Search backend 'searxng' lỗi ... Connection refused` | Bot và SearXNG không cùng Docker network / chưa chạy `--profile searxng`; kiểm tra `SEARXNG_URL=http://searxng:8080` |
| Engine upstream trả CAPTCHA/block (IP datacenter) | SearXNG tự chuyển engine khác; nếu ảnh hưởng chất lượng, dùng thêm backend fallback `ddgs`/`tavily` |
| Muốn xem UI để debug | Không mở port; dùng `docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/` (HTML) |

---

## 7. Cập nhật & backup

- Cập nhật code: `git pull && docker compose --profile searxng up -d --build`
- Cập nhật image: đổi `SEARXNG_IMAGE` trong `.env` sang tag ngày mới (khuyến
  nghị pin tag khi production) rồi `docker compose --profile searxng pull && docker compose --profile searxng up -d`
- Backup tối thiểu: `.env` + `searxng/settings.yml`. Cache SearXNG không cần backup.

---

## 8. Checklist production

- [ ] `searxng/settings.yml` đã tạo từ mẫu và `secret_key` đã đổi khỏi placeholder
- [ ] `.env` có `SEARCH_BACKEND=searxng` và `SEARXNG_URL=http://searxng:8080`
- [ ] Chạy bằng `docker compose --profile searxng up -d --build`
- [ ] Container SearXNG **không** publish port (`docker compose --profile searxng ps` không có cột PORTS)
- [ ] `docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/healthz` → `OK`
- [ ] Log bot không báo lỗi search; câu trả lời có 📚 nguồn

---

## 9. Nếu sau này bạn muốn public SearXNG

Cấu hình trong repo **cố tình không dành cho mode public** (không publish port,
không limiter, không valkey). Muốn public cho browser/người dùng thì đừng chỉ
thêm `ports:` vào service này — hãy làm theo hướng dẫn chính thức của SearXNG:
reverse proxy + HTTPS + `server.limiter: true` + Valkey + `trusted_proxies`,
tốt nhất dùng bộ `searxng-docker` riêng. Repo này chỉ cần instance private cho bot.
