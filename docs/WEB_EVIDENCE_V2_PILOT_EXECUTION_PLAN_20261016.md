# Kế hoạch thực thi: Web Evidence V2 — đóng freshness, pilot canary, độ bền production

Ngày 2026-10-16 · Cơ sở: `main` = `48b90e6` (CI run #1976 = success) · Flags hiện tại: `WEB_EVIDENCE_V2_ENABLED=false`, `WEB_AUTO_FETCH_ENABLED=false`

Mục tiêu: đưa dự án từ "merge được với flags OFF" tới "có đủ bằng cứng để bật pilot V2", đồng thời dọn các khoản P2 chặn `CONDITIONAL GO`.

## 0. Nguyên tắc bắt buộc

1. Flags giữ OFF tới khi Gate G2 đạt; mỗi gate phải để lại artifact tái lập được.
2. Không nới cap search (≤ 8), không hardcode URL/domain theo từng câu hỏi.
3. Không hạ chuẩn test để pass. Nếu hành vi đổi, viết lại test tường minh + nêu lý do trong commit.
4. Trước khi sửa symbol có caller: chạy `impact`; trước mỗi commit: `detect_changes`; index phải fresh (`node .gitnexus/run.cjs analyze --index-only`).
5. Rollback phải là một công tắc: tắt flag → hành vi V1 byte-for-byte.
6. Plan này được ghi lại ở chính file đang đọc.

## 1. Phase 1 (P1-A) — Chẩn đoán lớp lỗi freshness (KHÔNG sửa hành vi)

Điểm xuất phát đã xác minh bằng đọc code (không phải giả thuyết):

- `app/core/orchestrator.py:234` tính `verification_context` từ `request.text` (regex `chinh thuc|moi nhat|...` ở `app/search/source_ranking.py:_VERIFICATION_INTENT`) và truyền xuống `search_service.search(..., verification_context=True)` (quanh dòng 358). Vì `app/search/router.py:242` là `verify = quality_gate and (verification_context or verification_intent(normalized))`, ranker nhận `verify=True` ngay cả khi query do model tự đặt thiếu từ khoá freshness ⇒ loại giả thuyết "recency không bật vì query thiếu từ khoá".
- Recency slot `app/search/source_ranking.py:186-199` chỉ chạy khi `verify and len(chosen) == limit`; `recent` đòi `lexical_score >= MIN_RECENT_VERIFICATION_SCORE` (0.30) và `publication_age_days <= 365`; và **bỏ qua toàn bộ** nếu *bất kỳ* record recent nào đã nằm trong `chosen` (`not any(record in chosen ...)`).
- `_freshness_bonus` tối đa +0.15 (age ≤ 120 ngày) — không đủ lật một hit có lexical thấp.

Việc làm:

1.1 Probe `docs/acceptance/web_evidence_v2_freshness_20261016/probes/freshness_pool_probe.py`: gọi thật `search_service.search(<query Q6>)` (read-only), in bảng từng candidate: canonical URL, site, `lexical_score`, `publication_age_days`, `_freshness_bonus`, vào/không vào top-8, mã lý do.
1.2 Ma trận chẩn đoán phải phân biệt được:
   - **H1** hit mới không có trong pool backend (brave/google-cse suspended, DDGS lỗi) ⇒ lỗi tầng backend/đa dạng nguồn, không phải ranker.
   - **H2** có trong pool nhưng `lexical_score < 0.30` ⇒ recency slot không thấy nó.
   - **H3** có trong pool, `>= 0.30`, nhưng một record recent **khác** đã nằm trong `chosen` ⇒ guard `not any(...)` chặn swap.
   - **H4** có trong pool, `>= 0.30`, không recent nào khác trong `chosen` ⇒ nó đã được swap vào ⇒ thất bại ở tầng đọc/packing/synthesis, không phải retrieval.
1.3 Chạy 3 câu cùng lớp (không overfit 1 câu): Q6 (Artemis III) + 1 câu "theo trang chính thức" nhóm D/E + 1 câu fresh nhóm D khác, đều ở V2. Bộ 21 nằm trong `docs/WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md` (tracked) và được nạp bởi `scripts/web_evidence_live_smoke.py:load_questions`.
1.4 Ghi `FRESHNESS_DIAGNOSIS.md`: bảng + mã H đã xác nhận/loại + JSON bằng chứng + kết luận 1 dòng.

**Gate G1**: ≥ 1 mã H được xác nhận và ≥ 1 mã bị loại, kèm JSON; không sửa file production trong phase này.
Nếu **H1** ⇒ dừng nhánh ranker, chuyển sang hạng mục backend (engine SearXNG bị suspended / thêm backend dự phòng), vì sửa ranker không cứu được thứ không có trong pool.

## 2. Phase 2 (P1-B) — Sửa theo đúng nguyên nhân đã chứng minh

Chỉ làm mục ứng với mã H đã xác nhận ở G1.

- **2.1 (H3)** Sửa guard recency slot trong `app/search/source_ranking.py`: đổi "không có *bất kỳ* recent nào trong chosen" thành "hit mới nhất đủ liên quan *chưa* nằm trong chosen", và giữ slot dự phòng khi `verify`. Chạy `impact rank_web_results` trước khi sửa và công bố kết quả.
- **2.2 (H2)** Hạ có kiểm soát `MIN_RECENT_VERIFICATION_SCORE` cho **một** slot với ràng buộc: chỉ thay slot cuối, chỉ khi `verify`, chỉ với hit có `published_at` mới nhất trong nhóm; giữ nguyên ý nghĩa `test_dates_are_never_used_to_infer_freshness`.
- **2.3 (H1 phụ)** Truy vấn phụ có năm hiện tại ở `app/core/orchestrator.py` nhánh `web_search`: khi `verification_context` và top-8 chưa có nguồn nào ≤ 120 ngày, chạy **tối đa 1** truy vấn phụ `f"{q} {năm hiện tại}"`, tôn trọng cap ≤ 8 và ngân sách tool call.
- **2.4 (tuỳ chọn, chỉ sau G2)** Ưu tiên target là trang chính thức/mới trong `app/search/auto_fetch.py` khi `verify`.

Test bắt buộc mỗi thay đổi:

- Regression pin đúng cơ chế trong `tests/test_web_evidence_source_ranking.py`.
- Giữ xanh: `test_recency_slot_requires_query_intent_and_relevance`, `test_recent_published_sources_win_ties_only_for_freshness_queries`, `test_dates_are_never_used_to_infer_freshness`, `tests/test_web_search_limit_clamp.py`.

Fixture: thêm category `official_fresh` vào `scripts/generate_web_evidence_fixtures.py`, regenerate `tests/fixtures/web_evidence_v2/cases_100.json`; **SHA fixture đổi** ⇒ cập nhật baseline và công bố trong commit.

**Gate G2** (điều kiện duy nhất chặn pilot):
- `python tests/run_tests.py` xanh + `ruff` + markdown links + canonical env vocabulary.
- `python -m scripts.web_evidence_live_smoke --mode both` trên bộ 21, **≥ 2 lượt đầy đủ**, chấm rubric theo `QUALITY_EVALUATION.md`; điều kiện: V2 ≥ V1 ở **cả hai lượt**, 0 claim phụ sai trọng, guard error = 0.
- Lưu artifact JSON + bảng chấm + output `detect_changes` vào `docs/acceptance/web_evidence_v2_freshness_20261016/`.

## 3. Phase 3 (P1-C) — Pilot canary

- **3.1 Phạm vi canary**: thêm `WEB_EVIDENCE_V2_GROUP_IDS` để bật V2 theo nhóm trong cùng deployment; kèm test "nhóm ngoài allowlist giữ V1 byte-for-byte" và log rõ nhóm đang chạy V2.
- **3.2 Tín hiệu giám sát**: log `v2_citation_guard detected/allowed/removed/reasons/error`, `probes/citation_audit.py`, latency p50/p95, tỉ lệ câu trả lời chứa `[nguồn chưa xác minh]`.
- **3.3 Kill-switch**: thủ công + alert theo `PILOT_RUNBOOK.md §6`; tự động hoá chỉ sau khi 3.4 có số liệu.
- **3.4 Đo token/latency thật**: parse `usage` trong `app/ai/drivers/openai_chat.py` vào `CompletionResult`, trả `None` khi thiếu, kèm test offline.
- **3.5 Smoke thật (A04)**: exact image trên staging allowlist group — text, vision, tool, fallback, input sai, callback bị chặn; Crawl4AI vẫn OFF.

**Tiêu chí ra**: 48h canary không vi phạm stop condition + bảng trước/sau (latency, token, removed rate, chất lượng bộ 21).

## 4. Phase 4 (P2) — Vệ sinh & độ bền

- **4.1** Chốt số phận các tài liệu untracked. Luật rút từ CI #1975: **tài liệu tracked không được chứa link tới artifact untracked**.
- **4.2** Xoá `codex/v2-risk-hardening` (local + remote; đã trùng main).
- **4.3** Pin dependency (A07): sinh lock `==` từ image, CI cài từ lock, thêm job chạy suite trên Python patch mới.
- **4.4** Liveness (A06): heartbeat sau mỗi vòng polling + `healthcheck` trong compose.
- **4.5** Static type check: dựng pyright hoặc mypy ở chế độ báo cáo trước.

## 5. Thứ tự và phụ thuộc

| # | Việc | Phụ thuộc | Cỡ |
|---|---|---|---|
| 1 | Chẩn đoán freshness (G1) | — | S |
| 2 | Sửa theo mã H đã xác nhận | 1 | M |
| 3 | Matrix 21 câu × 2 lượt (G2) | 2 | M (cần provider thật) |
| 4 | Đo token/latency (3.4) | — (song song) | S |
| 5 | Canary theo nhóm (3.1) | 3 | M |
| 6 | Smoke Telegram thật (3.5) | 5 | S |
| 7 | Xoá branch + chốt tài liệu (4.1, 4.2) | — | XS |
| 8 | Lock dependency (4.3) | — | M |
| 9 | Liveness (4.4) | — | M |

## 6. Rủi ro và biện pháp

- Sửa ranker: `rank_web_results` từng CRITICAL trong impact ⇒ bắt buộc test pin "recent nhưng không liên quan không được vào top".
- Regenerate fixture đổi SHA baseline ⇒ phải ghi baseline mới.
- Matrix live cần provider thật, không tái lập offline ⇒ lưu artifact JSON + ghi rõ cấu hình.
- Canary trên deployment dùng chung: rollback = 1 flag.
- Index GitNexus tự công bố thiếu flow (146/346 entry point chưa rank) ⇒ không đọc "0 caller" là an toàn.
- Telegram/provider/latency/token production vẫn `NOT VERIFIED` cho tới khi 3.4 + 3.5 xong.

## 7. Bằng chứng tái lập nhanh

```
python tests/run_tests.py                       # 840 tests OK (skipped=11), exit 0
python -m ruff check .
python scripts/check_markdown_links.py          # chỉ đúng như CI khi chạy trên cây chỉ có tracked file
PYTHONPATH=. python scripts/web_evidence_benchmark.py --config v2 --fixture tests/fixtures/web_evidence_v2/cases_100.json
PYTHONPATH=. python scripts/web_evidence_benchmark.py --auto-fetch --fixture tests/fixtures/web_evidence_v2/cases_100.json
python -m scripts.web_evidence_live_smoke --mode both      # cần provider thật
```

## 8. Việc KHÔNG làm trong plan này

- Không bật bất kỳ flag nào trên production.
- Không bật Crawl4AI (còn `BLOCKED` chờ gate cô lập Linux/reboot).
- Không thêm database/job queue hay đổi kiến trúc state RAM.
- Không nới cap search, không thêm domain/URL cứng theo câu.
