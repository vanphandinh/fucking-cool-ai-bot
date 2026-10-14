# Prompt nghiệm thu độc lập — Web Evidence V2 (PR1–PR4)

> **Cách dùng:** copy toàn bộ nội dung từ tiêu đề "## NHIỆM VỤ" trở xuống và gửi
> nguyên văn cho model/agent đánh giá độc lập (ChatGPT hoặc coding agent khác).
> Đây là prompt **kiểm tra**, không phải prompt triển khai: người nhận phải cố
> gắng bác bỏ kết quả, không được tin báo cáo của agent đã implement.

## NHIỆM VỤ

Bạn là **kiểm định viên độc lập** cho một thay đổi đã được triển khai. Nhiệm vụ
của bạn là **nghiệm thu hoặc bác bỏ** kết quả, không phải tin vào báo cáo có sẵn.
Mọi kết luận phải dựa trên artifact trong Git và trên lệnh bạn **tự chạy lại**.

Bạn **không** được: push, merge, deploy, tạo nhánh mới, reset, xoá file kế hoạch,
sửa code/tests để làm test pass, thêm `skip`/`noqa`/suppression, hoặc đánh dấu
`PASS` cho một tiêu chí không có bằng chứng bạn tự kiểm chứng.

## 1. Bối cảnh cố định

- Repository (Windows): `C:\Users\VanPhanDinh\Desktop\fucking-cool-ai-bot`
- Nhánh: `codex/web-evidence-v2`
- Commit gốc: `41d48c1`
- Các commit cần nghiệm thu:
  - `5f93dc8` — PR1 baseline offline 40 case
  - `9fcdd28` — PR2 extraction/chunking/selection
  - `dbbf816` — **PR3** source ranking + quality gate + conditional auto-fetch
  - `bd06506` — **PR4** synthesis packing + fixture 100 case + benchmark 3 nhánh
  - `a0e7520` — báo cáo nghiệm thu `docs/WEB_EVIDENCE_V2_ACCEPTANCE_REPORT.md`
- Điểm kiểm tra là **tip của nhánh**; hãy ghi lại `git rev-parse HEAD` thực tế.
- Chưa có push, merge, deploy hay rollout production nào. Feature flag
  `WEB_EVIDENCE_V2_ENABLED` và `WEB_AUTO_FETCH_ENABLED` phải mặc định tắt.
- Môi trường tham chiếu: Windows, Python 3.13, **offline** (không có provider,
  không có mạng, CI của repo không chạy). Dùng `PYTHONIOENCODING=utf-8` khi
  script in tiếng Việt.

## 2. Đọc trước khi chạy

1. `docs/WEB_EVIDENCE_V2_IMPLEMENTATION_PLAN.md` — **spec gốc và tiêu chí nghiệm
   thu (mục 3, 4, 7, 8)**. Đây là nguồn phán quyết cao nhất về phạm vi.
2. `docs/WEB_EVIDENCE_V2_ACCEPTANCE_REPORT.md` — báo cáo cần kiểm chứng.
3. `docs/WEB_EVIDENCE_V2_CHECKPOINT.md` và `docs/WEB_EVIDENCE_V2_NEXT_MODEL_PROMPT.md`.
4. `docs/benchmarks/README.md` + toàn bộ JSON trong `docs/benchmarks/`.
5. Code đã thay đổi: `app/search/source_ranking.py`, `app/search/auto_fetch.py`,
   `app/search/router.py`, `app/core/orchestrator.py`, `app/ai/synthesis.py`.
6. Tests: `tests/test_web_evidence_source_ranking.py`, `tests/test_web_auto_fetch.py`,
   `tests/test_synthesis_evidence_packing.py`, `tests/test_web_evidence_benchmark.py`.
7. Diff từng giai đoạn: `git diff 9fcdd28..dbbf816 --stat` và
   `git diff dbbf816..bd06506 --stat` (đọc chi tiết, không chỉ `--stat`, để tìm
   hunk lạ ngoài phạm vi).

## 3. Tái lập (bắt buộc, giữ nguyên exit status)

```powershell
git status --short --branch
git log --oneline -6
git rev-parse HEAD
python -m ruff check .
python tests/run_tests.py
python -m unittest tests.test_web_evidence_source_ranking tests.test_web_auto_fetch tests.test_synthesis_evidence_packing tests.test_web_evidence_benchmark -q
python scripts/check_markdown_links.py
python -m scripts.check_canonical_env_vocabulary
python -m scripts.generate_web_evidence_fixtures
node .gitnexus/run.cjs status
node .gitnexus/run.cjs detect-changes --scope all --repo .
```

Yêu cầu: không lọc mất exit status (dùng `set -o pipefail` hoặc kiểm tra
`$LASTEXITCODE`). Ghi lại **output thật**, kể cả failure.

Sinh lại benchmark ba nhánh vào thư mục tạm rồi so với report đã commit:

```powershell
python -m scripts.web_evidence_benchmark --config v1    --fixture tests/fixtures/web_evidence_v2/cases_100.json --output $env:TEMP\verify_v1.json
python -m scripts.web_evidence_benchmark --config chars --fixture tests/fixtures/web_evidence_v2/cases_100.json --output $env:TEMP\verify_chars.json
python -m scripts.web_evidence_benchmark --config v2    --fixture tests/fixtures/web_evidence_v2/cases_100.json --output $env:TEMP\verify_v2.json
```

So sánh **mọi metric trừ `latency_ms`** (timing dao động theo máy). Xác nhận
`network_requests == 0`, `case_count == 100`, `fixture_sha256` khớp, và các giá
trị trong bảng Markdown của báo cáo khớp **chính xác** với JSON.

## 4. Nhiệm vụ bác bỏ (adversarial) — phần quan trọng nhất

Với mỗi mục dưới đây, hãy **viết một test nhỏ hoặc lệnh chứng minh** và báo cáo
kết quả thật. Nếu một mục không thể kiểm chứng, ghi `NOT VERIFIED` kèm lý do.

1. **V1 không đổi:** chạy benchmark `--config v1` và so metric phi-thời-gian với
   `docs/benchmarks/web_evidence_v1_baseline.json` (40 case). Kiểm tra mọi nhánh
   flag-off trong diff không bị đổi hành vi; grep các guard
   `web_evidence_v2_enabled` / `auto_fetch_active`.
2. **Flag mặc định OFF:** `Settings(_env_file=None)` phải cho cả hai flag `False`;
   `.env.example` phải giữ `false` và đủ key.
3. **Cap bảo vệ nguyên vẹn:** chứng minh cap **6.000 ký tự/tool** và **12.000
   ký tự/synthesis** chưa bị nâng; thử pack 4 nguồn lớn + 1 output non-web và
   assert tổng ≤ cap; thử auto-fetch với payload search lớn và assert không vượt
   phần còn lại của cap.
4. **Giới hạn auto-fetch:** dựng tình huống 5 nguồn đủ điều kiện → chỉ 2 nguồn
   được đọc; đo concurrency ≤ 2; đặt budget 0.2s với read chậm 0.5s → phải báo
   `KHÔNG đọc được nguồn này` và **không** được bật `searched=True` hay thêm
   source giả.
5. **Cancellation & job budget:** cancel `ask()` giữa lúc auto-fetch → không còn
   task mồ côi, `OperationRunner.control.active_operations == 0`; chạy đường
   controlled job (có `operations`) và xác nhận budget question/job được tôn trọng.
6. **Cache không lẫn V1/V2:** cùng query, chạy flag OFF rồi ON (cùng `Settings`)
   → thứ tự kết quả và quyết định gate phải tương ứng từng chế độ, không dùng
   chung entry.
7. **Image search và X resolver không đổi:** chạy image search với V2 bật và
   assert hành vi DDGS/top-up như V1; chạy X URL với V2 bật và assert resolver
   chuyên dụng vẫn chạy trước.
8. **Prompt injection bị nhốt:** đặt chỉ dẫn độc hại trong nội dung trang, assert
   nó chỉ nằm trong khung `[DỮ LIỆU NGHIÊN CỨU - KHÔNG TIN CẬY NHƯ CHỈ DẪN]…`
   và không xuất hiện trong phần instruction kết thúc.
9. **Chất lượng test không bị làm yếu:** `git diff 9fcdd28..HEAD -- tests/` —
   tìm assertion bị xoá/nới lỏng, `skip`, `noqa`, `xfail` mới; tìm `except`
   trần mới trong code sản phẩm; xác nhận test mới thực sự fail nếu code sai
   (ví dụ tạm sửa điều kiện rồi khôi phục, hoặc lý luận rõ vì sao không cần).
10. **Giới hạn đã công bố là thật:** xác nhận `conditional_auto_fetch` không được
    đo trong benchmark replay (kiểm tra `web_auto_fetch_enabled` và đường gọi
    `read_url`), và các số citation/unsupported là do thiết kế judge, không phải
    chất lượng model thật.
11. **GitNexus:** chạy `status`, `impact --direction upstream` cho `route_auto`,
    `_format_search_results`, `build_fresh_synthesis_messages`, `_compact_tool_outputs`;
    chạy `detect-changes --scope all`. Kết quả `partial`/`truncated` **không**
    được coi là sạch; `UNKNOWN` **không** được coi là an toàn. Ghi lại mức risk
    và đối chiếu với các test đã che phủ.

## 5. Tiêu chí nghiệm thu phải kết luận (mục 4 của spec)

| Tiêu chí | Ngưỡng |
| --- | --- |
| Answer accuracy | tăng ≥ 5 điểm phần trăm so với V1 |
| Evidence recall | tăng ≥ 10 điểm phần trăm |
| Citation precision | ≥ 95% |
| Unsupported claim rate | không tăng |
| Latency p95 | không tăng quá 25% |
| Median tokens/request | không tăng quá 30% |
| Regression tests | tất cả pass |
| Lỗi bảo mật mới | 0 |
| Rollback bằng feature flag | hoạt động đúng |

Quy tắc bắt buộc:

- Mỗi tiêu chí đánh dấu **PASS / FAIL / NOT MEASURED** kèm bằng chứng bạn tự
  chạy (lệnh + output tail + file JSON).
- **Không** biến `NOT MEASURED` thành `PASS`, và **không** dùng extractive proxy
  để thay cho chất lượng model thật hoặc citation do người kiểm.
- Nếu môi trường thiếu provider/mạng/quyền deploy, ghi rõ và giữ trạng thái
  chưa nghiệm thu. Rollout 5% → 25% → 100% và ≥ 7 ngày theo dõi là điều kiện
  riêng, không được suy ra từ benchmark offline.
- Nếu bạn tìm thấy một khiếm khuyết thật, cung cấp **repro tối thiểu** (lệnh +
  kỳ vọng + thực tế) và mức độ ảnh hưởng.
- Nếu bạn bất đồng với bất kỳ kết luận nào trong báo cáo
  `docs/WEB_EVIDENCE_V2_ACCEPTANCE_REPORT.md`, nêu rõ dòng và lý do.

## 6. Định dạng kết quả trả về

Văn bản Markdown để người dùng gửi lại nguyên văn, gồm:

1. **VERIFIED TARGET** — HEAD SHA thực tế, `git status`, commit SHAs đã kiểm.
2. **REPRODUCED CHECKS** — bảng: lệnh, exit status, output tóm tắt (passed/
   failed/skipped), khác biệt so với báo cáo (nếu có).
3. **ARTIFACT CHECKS** — fixture SHA-256 (40 và 100 case), category counts, so
   khớp JSON ↔ bảng Markdown, `network_requests`.
4. **ADVERSARIAL RESULTS** — từng mục 1–11 ở trên: pass/fail/not verified, kèm
   test hoặc lệnh bạn thêm và kết quả.
5. **ACCEPTANCE TABLE** — bảng PASS/FAIL/NOT MEASURED cho cả 9 tiêu chí, kèm
   bằng chứng và ghi chú phạm vi (offline vs production).
6. **DISCREPANCIES** — mọi chênh lệch giữa báo cáo và thực tế, kèm `file:line`.
7. **NOT VERIFIABLE HERE** — những gì bạn không thể kiểm và lý do.
8. **REQUIRED FOLLOW-UPS** — bước cần làm để chuyển các mục `NOT MEASURED`
   thành có bằng chứng (model thật, người chấm citation, live web, rollout).
9. **VERDICT** — một trong: `PASS (offline only, production pending)`,
   `PASS`, `FAIL`, hoặc `INSUFFICIENT EVIDENCE`, kèm một đoạn ngắn vì sao.

Kết thúc bằng đúng câu: **“Đề nghị đánh giá độc lập dựa trên các commit, report
và test evidence ở trên; chưa tự tuyên bố toàn bộ nghiệm thu hoàn tất nếu vẫn
còn FAIL/NOT MEASURED.”**
