# Web Evidence V2 — checkpoint bàn giao (2026-10-14)

**Mục đích:** lưu trạng thái kỹ thuật đã thực sự kiểm tra trước khi bàn giao
PR3/PR4 cho một coding agent khác. Nguồn yêu cầu có thứ tự ưu tiên là
`AGENTS.md`, `docs/README.md`, sau đó
`docs/WEB_EVIDENCE_V2_IMPLEMENTATION_PLAN.md`; source + tests hiện hành là
nguồn sự thật cho cách vận hành. **Không xóa kế hoạch trong lúc rollout.**

## Git và phạm vi

- Repository: `C:\Users\VanPhanDinh\Desktop\fucking-cool-ai-bot`.
- Nhánh làm việc: `codex/web-evidence-v2` (tạo từ `main` tại `41d48c1`).
- PR1 commit: `5f93dc8 test: establish web evidence v1 offline baseline`;
  commit này đã track file kế hoạch. PR2 nằm trong commit chứa checkpoint này;
  chạy `git log -3 --oneline` để lấy SHA hiện hành. Đây là **các giai đoạn
  triển khai logic trên nhánh**, chưa phải GitHub pull request đã mở/merge.
- Không push, merge hoặc deploy trong checkpoint này.
- PR1: hoàn thành; PR2: hoàn thành code và gate offline; PR3/PR4: chưa triển khai;
  production acceptance và theo dõi >=7 ngày: **chưa có bằng chứng**.
- Feature flags `WEB_EVIDENCE_V2_ENABLED=false` và
  `WEB_AUTO_FETCH_ENABLED=false` mặc định. Bật tắt V2 bằng flag là rollback.

## Những phần PR2 đã triển khai

- `app/search/evidence.py`: parser HTML bounded, bỏ chrome/navigation, chọn
  candidate tối đa 120k ký tự, chia chunk khoảng 1k/overlap 150, lexical
  relevance có gấp dấu tiếng Việt, bao phủ section/đoạn cuối, gắn
  `[SOURCE 1 / CHUNK n]` và URL, output tối đa 5.400 ký tự bao gồm metadata.
- `app/search/reader.py` và `app/search/url_service.py`: tích hợp V2 cho
  generic HTML, Jina và Crawl4AI; V1 nguyên đường đi khi flag off; X reader
  ưu tiên như cũ; trang chỉ có navigation không báo fetch thành công.
- `app/core/orchestrator.py`: chuyển query thực tế vào read URL khi bật V2.
- `app/config.py` và `.env.example`: V2 cùng các knobs auto-fetch dành cho PR3.
- `scripts/web_evidence_benchmark.py`: flag `--v2`, giữ V1 làm mặc định;
  `tests/test_web_evidence_v2.py` và
  `tests/test_web_evidence_benchmark.py`: regression/fixture tests.
- Giữ cap tải HTTP **2 MiB**, timeout, cancellation, SSRF, pinning IP,
  redirect restrictions, Crawl4AI isolation, output tool **6.000** và
  synthesis evidence **12.000** ký tự; không đổi giới hạn bảo vệ.

## Chứng cứ kiểm thử và benchmark tại checkpoint

Lệnh đã thực hiện trên Windows Python 3.13, 2026-10-14:

```powershell
python -m unittest tests.test_web_evidence_v2 tests.test_reader_article_focus tests.test_url_service tests.test_tool_url_dedupe -q
# 27 tests: OK
python -m unittest tests.test_web_evidence_v2 tests.test_reader_article_focus tests.test_url_service tests.test_tool_url_dedupe tests.test_web_evidence_benchmark -q
# 33 tests: OK, trước khi thêm 1 test navigation-only
python tests/run_tests.py
# 659 tests: OK (skipped=11); chạy lại sau sửa cuối PR2
python -m ruff check app/config.py app/core/orchestrator.py app/search/reader.py app/search/url_service.py app/search/evidence.py scripts/web_evidence_benchmark.py tests/test_web_evidence_v2.py tests/test_web_evidence_benchmark.py
# All checks passed
python -m scripts.web_evidence_benchmark --v2 --output docs/benchmarks/web_evidence_v2_pr2.json
# Wrote 40 results
```

40 snapshots tổng hợp offline, cùng SHA-256 của fixture
`1bf0e34571ea0360d06a348c8d930089e8174b5b60318a37060dab0c75c6323d`;
search/HTTP bị intercept, **0** truy cập mạng; mỗi case cố định fetch hai
nguồn. Hai report:
`docs/benchmarks/web_evidence_v1_baseline.json` và
`docs/benchmarks/web_evidence_v2_pr2.json`.

| Metric | V1 | PR2 V2 | Thay đổi |
| --- | ---: | ---: | ---: |
| Source recall@8 | 0.8627 | 0.8627 | 0 |
| Evidence recall | 0.4894 | 0.6383 | +14.89 pp |
| **Extractive proxy** answer accuracy | 0.4750 | 0.6500 | +17.50 pp |
| **Extractive proxy** citation precision | 1.0000 | 1.0000 | 0 |
| **Extractive proxy** unsupported claim rate | 0.0000 | 0.0000 | 0 |
| Estimated median tokens/request | 728 | 782 | +7.4% |
| Offline total latency p95 | 764.749 ms | 632.316 ms | -17.3% |
| Mean fetches | 2 | 2 | 0 |

**Không diễn giải proxy là chất lượng model**: judge chỉ trích `Dữ kiện:`
trực tiếp từ snapshot, nên citation precision 1.0 và unsupported rate 0 là
giới hạn thiết kế; latency là xử lý fixture local, token là char/4, không
phải chi phí hay latency production. Các ngưỡng nghiệm thu của PR4 vẫn chưa
được chứng minh.

GitNexus đã re-index trước checkpoint với 3.547 nodes, 8.222 edges,
215 flows; analyzer cảnh báo một số execution flows bị thiếu vì giới hạn
trace. Trước sửa lần gần nhất, upstream `select_evidence` = **CRITICAL**
(2 direct dependents, 6 processes), `read_url` = **HIGH** (2 direct, 4
processes). Do đó không hiểu các path vắng khỏi graph là an toàn. Khi làm tiếp
cần index lại và chạy impact trước mỗi symbol edit; chạy
`node .gitnexus/run.cjs detect-changes --scope all --repo .` trước commit;
không chấp nhận kết quả `partial`/`truncated` làm all-clear.

## Việc tiếp theo và điều kiện dừng

1. Xác minh `git status`, current HEAD, đọc đủ plan và các thay đổi hiện có;
   cập nhật GitNexus nếu index stale. Kiểm tra PR2 trên chính HEAD mới.
2. PR3: ranking source lexical title:snippet **2:1**, đủ >=2 domain độc lập
   điểm >=0.20 mới dừng SearXNG trong `auto`; DDGS top-up an toàn; kết quả
   tối đa 8, URL canonical dedupe, không ảnh hưởng image/X. Auto-fetch chỉ
   khi V2 + auto-fetch flags bật, tối đa 2 URL và budget 20s toàn câu hỏi,
   child cancellation/controlled jobs và request-scoped cache.
3. PR4: synthesis pack theo evidence <=12k gồm nhãn URL/chunk, không bỏ
   non-web tools; mở rộng fixture **100 cases** (30 fact, 20 multi, 20 buried,
   15 fresh, 15 unknown/conflict); so sánh đồng cấu hình V1 / chỉ tăng ký
   tự / adaptive V2; docs README, SEARCH_RESILIENCE, CRAWL4AI, `.env.example`;
   test V1 rollback.
4. Các mục acceptance **chưa đạt bằng chứng**: accuracy model thật +>=5pp,
   evidence recall +>=10pp, citation precision độc lập >=95%, unsupported
   claim không tăng, p95 <+25%, token median <+30%, zero security errors,
   tất cả regression pass. Cần kiểm chứng model và người chấm; báo rõ điều
   kiện không thể đo thay vì tự đánh dấu pass.
5. Rollout 5% → 25% → 100% chỉ khi có môi trường/quyền và gate phù hợp;
   quan sát **>=7 ngày sau khi đạt 100%**. Giữ file kế hoạch cho tới khi đủ
   toàn bộ mục 7 và 8; **không xóa theo lịch hoặc theo proxy benchmark**.

Prompt bàn giao cho agent khác nằm tại
`docs/WEB_EVIDENCE_V2_NEXT_MODEL_PROMPT.md`. Agent phải cung cấp biên bản
nghiệm thu kèm kết quả thực tế để người dùng gửi lại ChatGPT kiểm tra độc lập.
