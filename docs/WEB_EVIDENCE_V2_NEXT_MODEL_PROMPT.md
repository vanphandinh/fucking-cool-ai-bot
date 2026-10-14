# Prompt giao việc cho model coding tiếp theo — Web Evidence V2 PR3/PR4

Bạn là coding agent có quyền truy cập trực tiếp repository Windows:

`C:\Users\VanPhanDinh\Desktop\fucking-cool-ai-bot`

Người dùng yêu cầu **tiếp tục triển khai thực tế PR3, sau đó PR4**, kèm kiểm
thử, benchmark, tài liệu, commit và bằng chứng nghiệm thu để gửi ngược về
ChatGPT đang phụ trách đánh giá độc lập. Làm việc trên nhánh
`codex/web-evidence-v2` đã tạo từ `main`. Đừng tạo nhánh mới nếu không cần.
Không push, mở PR, merge hay deploy nếu không có chỉ đạo/quyền tương ứng.

**Đọc đầy đủ trước khi sửa:** `AGENTS.md`, `docs/README.md`,
`docs/WEB_EVIDENCE_V2_IMPLEMENTATION_PLAN.md`,
`docs/WEB_EVIDENCE_V2_CHECKPOINT.md`, `docs/benchmarks/README.md`, báo cáo
baseline và PR2. Kiểm tra `git status --short --branch`, `git log -4 --oneline`,
code/tests hiện tại; đừng ghi đè các thay đổi chưa commit. File kế hoạch đã
được commit từ PR1 (`5f93dc8`), **tuyệt đối không xóa** trước khi đủ 100%
rollout + 7 ngày theo dõi + mọi gate nghiệm thu.

**Quy trình bắt buộc:** nếu GitNexus stale, chạy
`node .gitnexus/run.cjs analyze --index-only`. Trước khi thay mỗi
function/class, chạy GitNexus `impact ... --direction upstream` (và graph
query/context khi tìm flow); cảnh báo HIGH/CRITICAL, đối chiếu các call path
không phân giải được; UNKNOWN không phải an toàn. Trước **mỗi commit**, chạy
`node .gitnexus/run.cjs detect-changes --scope all --repo .`; một kết quả
partial/truncated không được coi là sạch. Chỉ stage các file liên quan, giữ
PR3 và PR4 thành các commit có thể review riêng. Cập nhật checklist + nhật
ký của file kế hoạch sau mỗi giai đoạn, giữ rollback flag OFF mặc định.

**Thực hiện PR3 theo mục 3 của kế hoạch:** lexical ranking query-title
trọng số 2 và query-snippet 1, canonical URL dedupe và domain diversity,
`MAX_RESULTS=8`; không tác động image search hoặc X resolver. Ở
`SEARCH_BACKEND=auto`, chỉ chấp nhận SearXNG nếu >=2 nguồn từ hai domain độc
lập có lexical score >=0.20; nếu không, gọi DDGS top-up trong timeout hiện
có và merge/dedupe/rank. Không suy đoán freshness từ date thiếu. Auto-fetch
chỉ khi `WEB_EVIDENCE_V2_ENABLED` **và** `WEB_AUTO_FETCH_ENABLED` bật, thực
sự cần chứng cứ, tối đa 2 nguồn, concurrency <=2, tổng budget <=20 giây và
<=budget câu hỏi/job, ưu tiên URL người dùng, request-scoped canonical cache;
fail/timeout trả trạng thái thiếu evidence trung thực, không claim đã đọc.
Test đủ SearXNG kém/tốt, DDGS lỗi, duplicate domain/URL, stale cache,
singleflight, cancellation, multi-user jobs, no-web, direct URL, image và X.
Chạy đầy đủ regression và benchmark PR3; **chỉ qua PR4 khi gate PR3 đạt**.

**Thực hiện PR4:** synthesis evidence packing theo relevance/diversity,
giữ source URL/chunk label, tối đa 12.000 ký tự gồm metadata, bảo đảm các
non-web tool outputs vẫn được giữ hợp lệ và không thi hành prompt injection.
Tăng fixture tổng thành **100 trường hợp**: 30 fact, 20 multi-source, 20
buried, 15 fresh, 15 unknown/conflicting. Chạy so sánh đồng điều kiện **V1 /
chỉ tăng giới hạn ký tự / adaptive V2**; phân biệt offline proxy và model
thật. Có human review cho trích dẫn/chứng cứ tranh chấp, kết quả abstain,
URL nguồn và timestamp dữ liệu fresh. Cập nhật `README.md`, `docs/README.md`,
`docs/SEARCH_RESILIENCE.md`, `docs/CRAWL4AI_INTEGRATION.md`, `.env.example`,
các test liên quan. Giữ cơ chế SSRF/DNS pinning/redirect, tải 2 MiB,
timeout/cancellation/cache/circuit breaker, Crawl4AI isolation, cap 6k/tool,
cap 12k/synthesis. Test rollback bằng feature flag OFF.

**Các gate nghiệm thu cuối** (so cùng điều kiện V1, dựa trên mẫu đủ ý nghĩa):
accuracy trên answer model thật tăng >=5 điểm phần trăm; evidence recall tăng
>=10 điểm phần trăm; citation precision kiểm chứng độc lập >=95%; unsupported
claim rate không tăng; p95 latency không tăng quá 25%; median token/request
không tăng quá 30%; **0** lỗi bảo mật mới; tất cả regression tests pass.
Nếu thiếu access/provider/dữ liệu live, **ghi NOT MEASURED / BLOCKED**, không
tự cho pass bằng extractive proxy. Staged rollout 5% → 25% → 100% và ít nhất
7 ngày quan sát sau 100% chỉ khi đã có quyền, số liệu và môi trường phù hợp;
đừng tự triển khai production hoặc xóa plan. Nếu chưa đạt gate: để flag OFF,
giữ plan/checklist và ghi blocker.

**Lệnh khởi đầu đáng tin cậy** từ checkpoint; điều chỉnh theo code mới:

```powershell
git status --short --branch
node .gitnexus/run.cjs status
python -m unittest tests.test_web_evidence_v2 tests.test_reader_article_focus tests.test_url_service tests.test_tool_url_dedupe tests.test_web_evidence_benchmark -q
python tests/run_tests.py
python -m scripts.web_evidence_benchmark --v2 --output docs/benchmarks/web_evidence_v2_pr2.json
python -m ruff check .
```

**Bằng chứng baseline PR1/PR2 đang có:** 40-case fixture SHA-256
`1bf0e34571ea0360d06a348c8d930089e8174b5b60318a37060dab0c75c6323d`;
V1 evidence recall 0.4894, accuracy **extractive proxy** 0.475, p95 offline
764.749 ms, estimated median tokens 728; PR2 V2 evidence recall 0.6383,
accuracy **extractive proxy** 0.650, p95 offline 632.316 ms, tokens estimate
782; 0 network, 2 fetch/case; full PR2 suite 659 OK/11 skipped. Không
được trình bày các số này là kết quả nghiệm thu model thật.

**Khi hoàn thành, trả cho người dùng báo cáo nghiệm thu độc lập có thể gửi
nguyên văn lại ChatGPT đánh giá**, theo cấu trúc sau:

1. `COMPLETION STATUS`: PR3/PR4 DONE/PARTIAL/BLOCKED; nhánh/HEAD SHA, mỗi
   commit SHA, `git status`, có push/deploy/rollout hay chưa.
2. `CHANGES`: danh sách file và điểm thay đổi quan trọng; đường đi rollback;
   rủi ro GitNexus cho từng symbol quan trọng và kết quả `detect-changes`.
3. `TEST EVIDENCE`: **lệnh thực tế**, thời điểm/môi trường, tổng
   passed/failed/skipped, CI nếu có, regression security/timeout/cancel/cache,
   test bật/tắt flags; ghi rõ test nào chưa chạy hoặc thất bại.
4. `BENCHMARK EVIDENCE`: đường dẫn report JSON/CSV trong repo, fixture hash,
   số lượng và category counts, model/provider/config + timestamp, ba cột V1 /
   chars-only / V2 và delta ở **từng acceptance metric**, raw vs proxy vs
   human-reviewed, method, confidence/sample notes, latency p50/p95, tokens,
   fetch count, unsupported/citation per case. Từng chỉ tiêu đánh dấu
   **PASS / FAIL / NOT MEASURED** với dẫn chứng; đừng tự suy ra kết quả.
5. `ROLLOUT EVIDENCE`: tỷ lệ 5/25/100%, thời gian bắt đầu/kết thúc, logs
   aggregate/alerts, rollback drill, số ngày thực sự quan sát. Nếu chưa
   deploy, ghi `NOT STARTED` và giữ kế hoạch.
6. `BLOCKERS & FOLLOW-UPS`: các lỗi còn lại, vấn đề bảo mật, việc cần người
   dùng làm, checklist plan còn trống, danh sách bằng chứng cần lấy.

Kết thúc báo cáo bằng câu: **“Đề nghị ChatGPT đánh giá độc lập dựa trên các
commit, report và test evidence ở trên; chưa tự tuyên bố toàn bộ nghiệm thu
hoàn tất nếu vẫn còn FAIL/NOT MEASURED.”** Người dùng sẽ chuyển báo cáo này
về phiên ChatGPT ban đầu. Giữ toàn bộ artifact kiểm thử và file kế hoạch
trong Git để kiểm tra chéo.
