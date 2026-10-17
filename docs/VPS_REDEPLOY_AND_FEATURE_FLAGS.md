# VPS — chạy lại toàn bộ gate và bật tính năng

Runbook vận hành cho deployment Docker Compose trên VPS Linux. Mốc tham chiếu: `main` = `fc19b4e`, CI xanh trên 3.11 / 3.12 / 3.13 (test, audit, docker build, smoke config image).

Tài liệu này **không** thay thế [RELEASE_DECISION](acceptance/web_evidence_v2_independent_validation_20261016/RELEASE_DECISION.md): code đã merge với cờ tắt, còn việc *bật* cờ là quyết định vận hành có điều kiện riêng (mục 5 và 9).

---

## 0. Ba trạng thái cờ

| Trạng thái | `WEB_EVIDENCE_V2_ENABLED` | `WEB_AUTO_FETCH_ENABLED` | Ý nghĩa |
| --- | --- | --- | --- |
| A — V1 (mặc định) | `false` | `false` | Hành vi cũ, guard không chạy |
| B — V2 (canary hoặc toàn cục) | `true` (hoặc allowlist) | `false` | Evidence/ranking/guard V2 |
| C — V2 + Auto Fetch | `true` | `true` | Thêm đọc nguồn tự động (tối đa 2 nguồn, ngân sách ≤ 20s) |

`WEB_AUTO_FETCH_ENABLED` chỉ có hiệu lực khi V2 bật: `auto_fetch_enabled` trả `False` nếu thiếu một trong hai cờ (`app/search/auto_fetch.py`).

Đường an toàn nhất để "bật tính năng" là **canary**: giữ cờ toàn cục `false`, chỉ định chat id được dùng V2.

---

## 1. Chuẩn bị trên VPS

```bash
cd /srv/fucking-cool-ai-bot          # thư mục release thật của bạn
docker --version && docker compose version
python3 --version                    # >= 3.11
```

Bắt buộc có trước khi deploy:

- `.env` thật (mode `0600`), có `BOT_TOKEN`, provider key/model, `ALLOWED_GROUP_IDS`.
- `searxng/settings.yml` (copy từ `searxng/settings.example.yml`, đổi `secret_key`), `limiter: false`, `public_instance: false`, `search.formats` có `json`.
- `searxng/limiter.toml` mount read-only.

Chi tiết cài/soát SearXNG: [DEPLOY_SEARXNG_VPS.md](../DEPLOY_SEARXNG_VPS.md).

---

## 2. "Chạy lại toàn bộ" — bộ gate giống CI

Đây là bản sao các bước trong `.github/workflows/audit.yml`, chạy ngay trên VPS. Nguyên tắc: **không chép đè `.env` production**, không export `ALLOW_LIVE_NETWORK`, dùng venv tạm để không đụng interpreter hệ thống.

```bash
set -euo pipefail
cd /srv/fucking-cool-ai-bot
git fetch origin && git checkout main && git pull --ff-only
SHA=$(git rev-parse --short HEAD); echo "release=$SHA"

python3 -m venv /tmp/fcai-verify
. /tmp/fcai-verify/bin/activate
python -m pip install --upgrade 'pip>=26.2' 'setuptools>=83.0.0'
python -m pip install -r requirements.lock.txt --no-deps
python -m pip install -r requirements-audit.txt ruff==0.16.6 pip-audit==2.10.1

export PYTHONUTF8=1            # bắt buộc nếu stdout không phải UTF-8 (LANG=C, Windows)
unset ALLOW_LIVE_NETWORK       # tripwire mạng phải đang bật

python tests/run_tests.py                                   # 923 OK (skipped=11) ở mốc fc19b4e
python -m unittest discover -s tests -p 'test_*.py' -q       # CI chạy cả hai entrypoint
python -m ruff check .
python -m pip check
python -m compileall -q app tests scripts
python scripts/check_canonical_env_vocabulary.py
python scripts/check_markdown_links.py
python scripts/check_types.py                                # cần node/npx; đo pyright theo baseline
python -m pip_audit --progress-spinner off

docker compose config --quiet                                # cần .env thật tồn tại
docker compose --profile searxng config --quiet
bash -n scripts/crawl4ai_firewall.sh scripts/check_crawl4ai_vps_staging.sh \
  scripts/install_crawl4ai_firewall_boot.sh scripts/check_crawl4ai_report.sh \
  scripts/verify_crawl4ai_security.sh scripts/apply_crawl4ai_safe_boot.sh

docker build -t fcai-bot:$SHA .
docker run --rm --entrypoint python fcai-bot:$SHA \
  -c "from app.config import Settings; Settings(_env_file=None); print('image config OK')"
deactivate
```

Đọc kết quả đúng cách:

- `check_types.py` trả **2** khi pyright/npx không có hoặc version khác baseline — đó là *chưa kiểm được*, không phải pass.
- `pip-audit` đỏ nghĩa là lock đang pin một version đã bị công bố lỗ hổng; sửa lock rồi lặp lại, đừng bỏ bước.
- `run_tests.py` in `network tripwire: active`; nếu tripwire tắt thì suite không còn chứng minh offline.
- Có thể bỏ bước `discover` (trùng `run_tests.py`) nếu cần nhanh, nhưng đó là bước CI chạy.

---

## 3. Deploy / redeploy

```bash
cd /srv/fucking-cool-ai-bot
cp .env ".env.bak.$(date +%Y%m%d%H%M)"
python scripts/sync_env.py            # giữ value cũ, thêm key mới, siết mode 0600
docker compose --profile searxng config --quiet

docker compose --profile searxng up -d --build --force-recreate bot searxng
docker compose ps
docker compose logs --tail=80 bot
```

`sync_env.py` thoát `2` nếu `.env` có key không nằm trong `.env.example` — gỡ key đó ra (hoặc bổ sung vào example trong repo) rồi chạy lại; đừng bỏ qua lỗi này, vì nó chính là cái giữ `.env` khớp vocabulary.

Kiểm tra sống:

```bash
docker inspect --format '{{.State.Health.Status}}' fcai-bot      # healthy
docker compose exec -T bot python -m app.core.liveness; echo "liveness_exit=$?"
docker compose --profile searxng exec searxng wget -qO- http://127.0.0.1:8080/healthz   # OK
```

Dòng log phải thấy khi bot khởi động:

```
web_evidence_v2 scope=off policy=v1            # trạng thái A
web_evidence_v2 scope=canary groups=1 policy=v2-for-allowlist
web_evidence_v2 scope=global policy=v2
```

Nếu không thấy dòng `web_evidence_v2 scope=…`, bot đang chạy image cũ — rebuild lại, `restart` không áp dụng env/image mới.

---

## 4. Smoke sau deploy (trước khi bật cờ)

```bash
# 1) SearXNG JSON: general + images phải có results > 0
docker compose --profile searxng exec -T bot python - <<'PY'
import httpx
from app.config import Settings
s = Settings(); url = s.searxng_url.strip().rstrip("/")
for category in (None, "images"):
    params = {"q": "Hà Nội", "format": "json"}
    if category: params["categories"] = category
    r = httpx.get(f"{url}/search", params=params, headers={"Accept": "application/json"}, timeout=30)
    print(category or "general", r.status_code, len(r.json().get("results") or []), r.json().get("unresponsive_engines"))
PY

# 2) Đọc URL X trực tiếp (luồng riêng, không qua search)
docker compose exec -T bot python - <<'PY'
import asyncio
from app.config import Settings
from app.search.url_service import read_url
r = asyncio.run(read_url("https://x.com/jack/status/20", Settings(), mode="auto"))
print("ok:", r.ok, "backend:", r.backend)
PY
```

Trong Telegram (người thật, nhóm allowlist):

1. `/web_search` một dữ kiện thời sự → nguồn ≤ 8, tin nhắn tới được.
2. Câu không cần web → không search.
3. Gửi ảnh → trả lời vision, không lỗi parse.
4. Đọc URL thường + URL X → có nội dung hoặc báo lỗi rõ ràng.
5. Log không có traceback, không `TelegramBadRequest` lặp.

---

## 5. Bật tính năng

### 5.1 Canary V2 — khuyến nghị

```env
WEB_EVIDENCE_V2_ENABLED=false
WEB_EVIDENCE_V2_GROUP_IDS=-1001234567890,-1009876543210
WEB_AUTO_FETCH_ENABLED=false
```

```bash
docker compose up -d --no-deps --force-recreate bot
docker compose logs --tail=20 bot | grep web_evidence_v2     # scope=canary
```

Chat ngoài danh sách (và request không có chat id) giữ nguyên hành vi V1. Rollback: xoá danh sách → recreate.

### 5.2 V2 toàn cục

```env
WEB_EVIDENCE_V2_ENABLED=true
WEB_EVIDENCE_V2_GROUP_IDS=
WEB_EVIDENCE_MAX_EXTRACTED_CHARS=120000
```

Đây là bước vượt qua kết luận pilot hiện tại: trên bộ 21 chuẩn, V2 đạt 19/21 so với V1 20/21, và lỗi Q6 (nguồn cũ khi query chỉ nói "trang chính thức của X") tái hiện 3/3 lượt. Bật toàn cục là quyết định có ý thức, không phải hệ quả của việc merge.

### 5.3 Auto Fetch (trạng thái C)

```env
WEB_EVIDENCE_V2_ENABLED=true
WEB_AUTO_FETCH_ENABLED=true
WEB_AUTO_FETCH_MAX_SOURCES=2      # trần cứng của config là 2
WEB_AUTO_FETCH_BUDGET_SEC=20      # trần cứng là 20
```

Chỉ bật sau khi B đã chạy ổn và các mục token/latency production đã có số. Theo dõi log đọc nguồn, redirect/SSRF bị chặn, và thời gian đọc.

### 5.4 Crawl4AI — chỉ sau gate live riêng

Đừng bật cùng lúc với V2. Trình tự bắt buộc (chi tiết: [CRAWL4AI_INTEGRATION.md](CRAWL4AI_INTEGRATION.md)):

```bash
# sinh token rồi dán vào .env: CRAWL4AI_API_TOKEN=...
# chỉ chia sẻ giữa bot và container crawler, không commit
openssl rand -hex 32

docker compose --profile crawl4ai pull crawl4ai crawl4ai-proxy crawl4ai-dns
docker compose --profile crawl4ai up -d --no-deps crawl4ai-dns crawl4ai-proxy crawl4ai
sudo bash scripts/crawl4ai_firewall.sh apply
sudo bash scripts/crawl4ai_firewall.sh check
sudo python3 scripts/check_crawl4ai_vps_live.py --live     # bot vẫn đang tắt crawler
sudo bash scripts/apply_crawl4ai_safe_boot.sh              # policy restart + báo cáo read-only
```

Chỉ khi mọi bước trên pass mới đặt `CRAWL4AI_ENABLED=1` rồi `docker compose up -d --no-deps --force-recreate bot`. `crawl4ai_ready` yêu cầu đúng `CRAWL4AI_URL=http://crawl4ai:11235` và `CRAWL4AI_EGRESS_PROXY_URL=http://crawl4ai-proxy:3128`; URL khác thì bot lặng lẽ dùng generic reader.

### 5.5 Các cờ khác

| Biến | Mặc định | Ghi chú |
| --- | --- | --- |
| `VISION_ENABLED` | `1` | Tắt được mà không đổi luồng text |
| `X_FETCH_ENABLED` | `1` | `0` → chỉ còn generic reader |
| `SEARCH_BACKEND` | `auto` | `searxng` / `ddgs` là strict, không fallback ngầm |
| `QUESTION_CONTROLS_ENABLED` | `0` | Job renewable chỉ nằm trong RAM: restart mất job |

---

## 6. Đo lại sau khi bật

```bash
# A/B trên bộ 21 câu, không auto-fetch (so sánh công bằng)
PYTHONUTF8=1 python -m scripts.web_evidence_live_smoke \
  --mode both --no-auto-fetch --timeout 180 \
  --output /tmp/smoke_ab_$(date +%Y%m%d).json

# Tập trung vào ca đã biết là yếu
PYTHONUTF8=1 python -m scripts.web_evidence_live_smoke \
  --mode v2 --ids 6,15 --output /tmp/smoke_q6_q15.json

# Số liệu vận hành + stop condition (offline, đọc artifact)
python scripts/web_evidence_pilot_audit.py /tmp/smoke_ab_$(date +%Y%m%d).json
```

`web_evidence_pilot_audit.py` in latency p50/p95, token usage thật (nếu adapter trả), tỉ lệ guard phải sửa, tỉ lệ marker "nguồn chưa xác minh", URL ngoài tập evidence, và verdict stop-condition. Exit `0` chỉ nghĩa "không thấy stop condition trong artifact", **không** phải kết luận chất lượng.

Artifact thu được vẫn cần chấm tay theo rubric: xem `docs/WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md` và [QUALITY_EVALUATION.md](acceptance/web_evidence_v2_independent_validation_20261016/QUALITY_EVALUATION.md).

---

## 7. Rollback

```bash
# về V1 byte-for-byte
sed -i 's/^WEB_EVIDENCE_V2_ENABLED=.*/WEB_EVIDENCE_V2_ENABLED=false/' .env
sed -i 's/^WEB_AUTO_FETCH_ENABLED=.*/WEB_AUTO_FETCH_ENABLED=false/' .env
sed -i 's/^WEB_EVIDENCE_V2_GROUP_IDS=.*/WEB_EVIDENCE_V2_GROUP_IDS=/' .env
docker compose up -d --no-deps --force-recreate bot
docker compose logs --tail=20 bot | grep web_evidence_v2        # scope=off policy=v1
```

- Không cần rollback DB/schema: bot không có persistence ngoài RAM; cache V1/V2 tách namespace `:v2`.
- Crawl4AI: `CRAWL4AI_ENABLED=0` + recreate bot; giữ nguyên firewall và rules bảo vệ.
- Nếu lỗi nằm ở code, quay lại image/commit trước: `git checkout <sha> && docker compose up -d --build --force-recreate bot searxng`.

---

## 8. Stop conditions (tắt V2 ngay)

- Guard crash ≥ 1 (mất câu trả lời của người dùng).
- Bypass citation nghiêm trọng mới phát hiện trên model thật.
- Marker `[nguồn chưa xác minh]` xuất hiện ở phần lớn câu trả lời có search (≈ > 10%) — evidence pack đang hỏng.
- Cùng một lỗi "nguồn cũ" lặp ≥ 2 lần/ngày ở nhóm câu thời sự.

---

## 9. Giới hạn đã biết (đừng đọc là đã xong)

- **Chưa có bằng chứng production**: chưa đo latency/token dưới tải thật; các số hiện có là pilot-scale.
- **Retrieval yếu với query mơ hồ** ("trang chính thức của X"): hit news-release mới không vào top-8, auto-fetch không cứu được. Đây là điều kiện còn thiếu để bật pilot theo RELEASE_DECISION.
- **Crawl4AI**: isolation/firewall/reboot mới chỉ kiểm offline + staging; chưa có bằng chứng trên VPS thật.
- **`web_evidence_live_smoke.py --list`** cần `PYTHONUTF8=1` khi stdout không phải UTF-8 (Windows cp1252, SSH `LANG=C`), nếu không sẽ `UnicodeEncodeError` khi in câu hỏi tiếng Việt.
- **Telegram render** cho URL schemeless/protocol-relative/zero-width vẫn là UNCONFIRMED.
