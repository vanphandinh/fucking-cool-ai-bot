# Web Evidence V2 — đánh giá live sau hardening (2026-10-18)

Tài liệu này chấm tay các lượt chạy live sau thay đổi P0–P2 (SearXNG loopback,
preflight, discovery slot, cảnh báo chứng cứ, fail-closed khi search lỗi).
Artifact vẫn `NOT MEASURED`; pilot audit exit 0 chỉ nghĩa là không có stop
condition kiểm tra được bằng máy.

## 1. Môi trường

- SearXNG chạy loopback qua `docker-compose.smoke.yml` (`127.0.0.1:8080`),
  healthz 200.
- Mỗi lượt đều chạy preflight: `search_preflight.ok = true`, 8 kết quả.
- Lệnh: `SEARXNG_URL=http://127.0.0.1:8080 python -m scripts.web_evidence_live_smoke ...`
- Engine `brave` của SearXNG bị treo (`Suspended: too many requests`) trong phần
  lớn thời gian; kết quả phụ thuộc các engine còn lại.

## 2. Artifact

| Artifact | Câu | Audit | Ghi chú |
| --- | ---: | --- | --- |
| `run_p1_q6q15_a.json` | 2 | exit 0 | trước khi thêm `update` vào truy vấn discovery |
| `run_p1_q6q15_b.json` | 2 | exit 0 | sau khi thêm `update` |
| `run_p1_q6_c.json` | 1 | exit 0 | sau khi ưu tiên trang "change-announcing" |
| `run_p1_full_1.json` | 21 | exit 0 | p50 12.0s / p95 44.2s; guard removed 0 |
| `run_p1_full_2.json` | 21 | exit 0 | p50 12.3s / p95 28.1s; guard removed 0 |
| `run_1.json`, `run_2.json` | 21 | exit 0 | bộ cũ trước P0–P2 (giữ làm mốc so sánh) |

## 3. Chấm tay Q6 (Artemis — lần hạ cánh có phi hành đoàn)

| Lượt | Kết quả | Verdict |
| --- | --- | --- |
| `run_p1_q6q15_a` | Artemis III là lần hạ cánh đầu tiên, dời sang 2028 không nêu số mệnh | FAIL |
| `run_p1_q6q15_b` | Artemis IV | PASS |
| `run_p1_full_1` | "một sứ mệnh Artemis tiếp theo, dự kiến 2028" | FAIL |
| `run_p1_full_2` | Artemis IV | PASS |
| `run_p1_q6_c` | Artemis IV | PASS |

**Q6: 3/4 lượt đạt sau thay đổi, nhưng chỉ 1/2 lượt full đạt → chưa thỏa tiêu chí
"đúng ở cả hai lượt".**

## 4. Chấm tay Q15 (sự kiện công nghệ lớn trong tháng)

| Lượt | Kết quả | Verdict |
| --- | --- | --- |
| `run_p1_q6q15_a` | "OpenAI DevDay 4/10 ra mắt GPT-6 Astra" — claim không có nguồn hỗ trợ | FAIL |
| `run_p1_q6q15_b` | GITEX Vietnam, VEC 13–15/10, Semiexpo 2026 — có nguồn | PASS |
| `run_p1_full_1` | FPT TRANSFORM 17/10, GITEX Vietnam — có nguồn | PASS |
| `run_p1_full_2` | VEC 13–15/10, VEEW 17–19/10, GITEX Vietnam — có nguồn | PASS |

**Q15: 3/4 lượt đạt sau thay đổi; claim bịa kiểu DevDay/GPT-6 không lặp lại trong
ba lượt gần nhất.**

## 5. Kiểm chứng offline

- `python tests/run_tests.py`: 925 tests OK (skipped=11).
- Ruff, compileall, Markdown link scan, `git diff --check`: pass.
- `scripts/check_types.py`: 55 lỗi, bằng baseline.
- Benchmark: 40-case và 100-case không đổi metric. `--auto-fetch` sau khi thêm
  backfill đọc bù: `max_two_sources` và `attempts_within_ceiling` đều true,
  evidence delivery 0.383 → 0.4681, mock read attempts 90 → 162,
  `request_tokens_median_estimate` 435.5 → 526.0 (header cảnh báo cộng chứng cứ
  đọc bù). Chi tiết và cách diễn giải ở `docs/benchmarks/README.md`; fixture
  chỉ ghi vài trang đọc được nên replay kích hoạt backfill thường xuyên hơn
  production.

## 6. Kết luận

- **Q15: đạt yêu cầu sau thay đổi** (cần chấm tay tiếp nếu muốn chốt GO).
- **Q6: cải thiện rõ nhưng chưa đạt cổng 2/2 lượt full.** Nguyên nhân còn lại:
  model tự sinh truy vấn neo vào Artemis III và đọc trang kế hoạch cũ; slot
  discovery đôi khi trỏ trang recap thay vì trang công bố thay đổi; budget chỉ
  cho đọc 2 nguồn nên một lần đọc lỗi làm mất cơ hội đọc trang cập nhật.
- **Không có tuyên bố chất lượng production.** Pilot vẫn ở trạng thái
  `PENDING LIVE TEST` cho tới khi cả hai lượt full đạt ngưỡng mục 4 của
  `docs/WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md`.

## 7. Bước tiếp theo đề xuất

1. Query-planning guard: khi câu hỏi hỏi "sứ mệnh nào", bắt buộc trả lời tên mệnh
   từ trang có ngày cập nhật; nếu chỉ có trang không ngày, phải nêu chưa kiểm chứng.
2. Mở rộng auto-fetch: khi một read lỗi và còn ngân sách, thử candidate kế tiếp
   (hiện tối đa 2 nguồn và không bù khi lỗi).
3. Theo dõi engine SearXNG; cân nhắc bật lại engine cung cấp ngày đăng để
   freshness ranking hoạt động.
4. Chạy lại 2×21 sau các bước trên và chấm tay đầy đủ 21 câu.

## 8. Lượt live P2 sau backfill (2026-10-18, 2 × 21 câu)

Hai lượt full chạy sau khi thêm backfill đọc bù, cùng provider/model,
`WEB_AUTO_FETCH_*` và timeout 180s. SearXNG loopback healthy; preflight
`ok=true` (8 và 7 kết quả).

### 8.1 Artifact và vận hành

| Artifact | Câu | Audit | p50 / p95 | Prompt token median |
| --- | ---: | --- | --- | ---: |
| `run_p2_full_1.json` | 21/21 completed, 0 timeout | exit 0 | 10.7s / 40.4s | 6051 |
| `run_p2_full_2.json` | 21/21 completed, 0 timeout | exit 0 | 12.7s / 39.3s | 4830 |

Guard: 0 URL bị xoá, 0 lỗi, 0 citation bịa ở cả hai lượt. Model chain ghi trong
artifact: `cl/cline-free/mimo-v2.6-flash`, `cl/cline-free/muse-spark-1.3-contributor`,
`qwen/qwen3.8-max:free` (free tier).

### 8.2 Chấm tay

| Câu | Lượt 1 | Lượt 2 |
| --- | --- | --- |
| 1–5, 7–14, 16–21 | PASS | PASS |
| Q6 (Artemis) | **FAIL** — nói "hạ cánh có phi hành đoàn … 2028", không nêu số mệnh; III chỉ còn test LEO nhưng không có Artemis IV | **FAIL** — khẳng định Artemis III là lần hạ cánh (lặp plan cũ) |
| Q15 (sự kiện tháng) | PASS — FPT TRANSFORM 17/10, có nguồn FPT/techz | **FAIL** — "OpenAI DevDay 4/10: ra mắt GPT-6 Astra tại DevDay"; chuẩn đã xác minh là DevDay 29/9 và Astra 3/9 |

- **Bộ 21: lượt 1 = 20/21; lượt 2 = 19/21** (đạt ≥18/21 ở cả hai).
- **Cổng GO mục 4 KHÔNG đạt**: Q6 sai 0/2 lượt và có một claim sai trọng yếu
  (Q15 lượt 2).

### 8.3 Vì sao backfill không cứu được Q6

Ở **cả hai** lượt, Q6 chỉ đọc được đúng **một** nguồn:
`https://www.nasa.gov/missions/artemis/artemis-3/nasa-outlines-preliminary-artemis-iii-mission-plans`
— trang kế hoạch cũ, và nó **đọc thành công** nên được dùng làm chứng cứ.
Artifact không ghi read thứ hai có lỗi hay không, nên **không thể nói backfill có
chạy hay không**; nó chỉ cho thấy nguồn cập nhật không vào được câu trả lời.

Probe trực tiếp ngay sau đó (cùng `url_service`, không qua model): cả ba URL
— trang cũ, `/artemis-iii-news-and-updates`, `/mission/artemis-iv` — đều
`ok=True` (5.400 ký tự excerpt), trang Artemis IV có `Updated: Mar 16, 2026`.
Vậy nội dung cập nhật **có sẵn và đọc được**; nút thắt không phải "nguồn chết"
mà là **chọn nguồn để đọc** (trang cũ thắng lexical, discovery slot chỉ mang về
một kết quả mới) và **ràng buộc câu trả lời** (nếu không có trang có ngày xác
nhận thì phải nêu chưa kiểm chứng thay vì lặp plan cũ). Đây đúng là mục 7.1,
chưa làm.

### 8.4 Giới hạn của phép đo

- Artifact live **không ghi số lần đọc thử** của auto-fetch, nên tần suất và
  hiệu quả backfill trong production vẫn là **chưa đo**; bằng chứng cho backfill
  hiện chỉ đến từ test và replay offline (evidence delivery 0.383 → 0.4681,
  4 case tăng chứng cứ).
- Hai lượt này dùng model free-tier, và SearXNG vẫn báo `brave` bị suspend; kết
  quả không đại diện cho model mạnh hơn hay backend đầy đủ engine.
- Không có tuyên bố chất lượng production; trạng thái vẫn `PENDING LIVE TEST`.
