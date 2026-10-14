# Kế hoạch triển khai Web Evidence V2 — Search, Fetch và Synthesis

> **Trạng thái:** IN PROGRESS — PR1–PR4 code, kiểm thử và gate offline hoàn thành; **nghiệm thu model thật, citation do người kiểm và rollout production chưa thực hiện**. Chỉ xóa sau nghiệm thu và 7 ngày theo dõi.
> **Ngày lập:** 2026-10-14.
> **Áp dụng cho:** `fucking-cool-ai-bot`.
> **Quy tắc lưu trữ:** giữ file này trong working tree khi triển khai; bảo đảm file đã được commit vào Git trước khi xóa; xóa file bằng một commit dọn dẹp cuối cùng sau khi tất cả điều kiện ở mục 8 đạt. Bản kế hoạch hoàn thành chỉ tồn tại trong Git history, phù hợp `docs/README.md`.

## 1. Mục tiêu, phạm vi và quyết định đã chốt

**Mục tiêu chính:** cải thiện độ chính xác câu trả lời sử dụng web bằng cách tìm đúng nguồn, lấy được chứng cứ kể cả nằm sâu trong trang và cấp phát ngữ cảnh cho model theo độ liên quan, trong khi vẫn kiểm soát thời gian, token và bảo mật.

- Không bỏ giới hạn ký tự/byte/token một cách vô hạn. **Đọc đủ để tìm đoạn đúng, sau đó mới giới hạn dữ liệu đưa vào model.**
- Giữ public tool contract hiện có: `web_search(query)` trả kết quả `{title, url, snippet}`; `fetch_url(url, mode)` vẫn dùng giao diện hiện tại. Không thêm tham số `section` trong V2 đầu tiên.
- Không đổi chính sách search khi không cần thông tin bên ngoài; ưu tiên `fetch_url` nếu người dùng đưa URL; không thay đổi image search hoặc X-specific resolver.
- Không thêm vector database, embedding hay reranker dựa vào LLM trong V2. Trước tiên dùng lexical relevance có xử lý tiếng Việt/tiếng Anh, đánh giá bằng benchmark rồi mới cân nhắc các thành phần phức tạp.
- Không tăng ngay giới hạn **6.000 ký tự/tool** trong `app/ai/router.py` và **12.000 ký tự synthesis evidence** trong `app/ai/synthesis.py`; thay logic chọn chứng cứ trước, đo hiệu quả, rồi mới xem xét tăng ngân sách.
- Các thay đổi có thể rollback phải nằm sau feature flag và tắt theo mặc định. Bảo vệ SSRF, byte limit, kiểm tra redirect/DNS, cancellation, timeout và cô lập Crawl4AI vẫn là điều kiện bắt buộc.

### Hiện trạng đã xác nhận bằng đọc repository

| Tầng | Hiện trạng | Rủi ro |
| --- | --- | --- |
| Search | `app/search/service.py`: `MAX_RESULTS=8`; SearXNG/DDGS cắt snippet ~400 ký tự | Thiếu thông tin để đánh giá nguồn |
| Auto routing | `app/search/router.py`: SearXNG có >=2 kết quả web thì có thể dừng không cần DDGS | Hai kết quả kém liên quan vẫn được chấp nhận |
| Search formatting | `app/core/orchestrator.py`: query cắt 300 ký tự; format snippet 300 ký tự | Mất tín hiệu của truy vấn dài và snippets |
| Generic reader | `app/search/reader.py`: đầu ra 8.000 ký tự, đầu tải tối đa 2 MiB | Đoạn chứng cứ nằm sau 8.000 ký tự bị bỏ qua |
| Crawl4AI | `app/search/crawl4ai_client.py`/`app/config.py`: mặc định 12.000, trần cấu hình 50.000 ký tự | Nguồn dài có thể bị cắt trước lúc chọn chứng cứ |
| Tool execution | `app/ai/router.py`: `str(output)[:6000]` | Nội dung reader dài hơn vẫn bị cắt |
| Synthesis | `app/ai/synthesis.py`: tổng 12.000 ký tự, chia ngân sách tương đối đều theo output | Nguồn ít liên quan chiếm phần bằng nguồn quan trọng |

**Lưu ý:** các ngưỡng là giới hạn trong code tại thời điểm lập kế hoạch, không phải kết quả benchmark. Trước khi sửa phải kiểm tra lại `HEAD` và index GitNexus (index có thể cũ hơn source hiện tại).

## 2. Luồng mục tiêu và interface nội bộ

```text
User question / direct URL
    -> Search (SearXNG; DDGS top-up khi thiếu chất lượng hoặc đa dạng)
    -> URL normalization + dedupe + lexical rank + source diversity
    -> Selective fetch (tối đa 2 nguồn, có điều kiện và budget riêng)
    -> Reader/Jina/Crawl4AI: bounded download + content extraction
    -> Sections / chunks + relevance scoring + coverage selection
    -> Evidence packing theo source và câu hỏi (<=6k/tool; <=12k synthesis)
    -> Synthesis với source attribution và xử lý bất định
```

**Kiểu dữ liệu nội bộ đề xuất:** `EvidenceChunk(source_url, canonical_url, title, heading, ordinal, text, score)` và `EvidenceBundle(query, chunks, truncated, fetch_status)`. Có thể dùng `dataclass`/`TypedDict` nhất quán với module hiện tại; không đưa kiểu này thành public API. Mỗi chunk phải truy ngược được URL và vị trí tương đối; metadata của trang là dữ liệu, tuyệt đối không được diễn giải thành chỉ dẫn cho model.

**Chiến lược trích xuất:** giới hạn tải HTML/Jina như hiện hành, làm sạch `nav`, `footer`, `header`, `script`, `style`, quảng cáo/chrome lặp, bảo toàn heading/paragraph/list khi có thể. Chia văn bản thành chunk mục tiêu **1.000 ký tự**, overlap **150 ký tự**, không cắt giữa từ/câu khi dễ tránh. Quét nội dung tối đa **120.000 ký tự text/trang** ở tầng nội bộ generic reader (feature flag bật), sau đó chấm điểm chunk trước khi cắt evidence cho model; giữ khả năng phát hiện đoạn ở vị trí >8.000. Với Crawl4AI, giữ trần cấu hình hiện có 50.000 cho V2 ban đầu và không cố tăng lên 120.000. Tránh lưu nguyên nội dung dài trong logs hoặc cache dùng chung.

**Chấm điểm mặc định (không dùng mô hình bổ sung):** chuẩn hóa Unicode/case, có bản gấp dấu để đối sánh tiếng Việt, tách tokens và bỏ stopwords phổ biến; điểm lexical có trọng số tiêu đề/heading cao hơn body, thưởng cụm từ chính xác, phạt boilerplate; khi sắp xếp dùng đa dạng đoạn/nguồn để tránh nhiều chunk gần như trùng. Với yêu cầu tóm tắt toàn trang, chọn thêm đại diện từ các section chính theo thứ tự tài liệu để bảo đảm độ bao phủ, thay vì chỉ top lexical chunks. Câu hỏi cần nhiều nguồn ưu tiên chứng cứ từ ít nhất hai domain độc lập nếu có.

**Định dạng chứng cứ gửi model:** mỗi đoạn có nhãn nguồn ổn định (`[SOURCE n / CHUNK m]`), URL chính tắc, tiêu đề, heading và nội dung thực tế; giới hạn được tính **bao gồm nhãn/metadata**. Giữ nhãn nguồn qua lần compact cuối; không cho phép tuyên bố đã đọc/kiểm chứng trang nếu fetch thất bại. Không coi snippet tìm kiếm là bằng chứng xác minh độc lập khi website chưa được đọc.

**Cache:** tái sử dụng cache/singleflight/circuit breaker hiện có; cache nội dung thô và chunk chỉ trong phạm vi request (theo canonical URL), nhưng điểm/rank và lựa chọn evidence phải theo từng câu hỏi. Không vô tình cache một bản trích chọn theo câu hỏi rồi trả lại cho câu hỏi khác.

**Cấu hình V2 (bổ sung vào `Settings` và `.env.example` khi triển khai):**

| Tên | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `WEB_EVIDENCE_V2_ENABLED` | `false` | Tắt/bật toàn bộ pipeline evidence V2; rollback về V1 |
| `WEB_EVIDENCE_MAX_EXTRACTED_CHARS` | `120000` | Ngưỡng text candidate nội bộ generic reader; không phải model context |
| `WEB_AUTO_FETCH_ENABLED` | `false` | Cho phép auto-fetch khi đang bật V2; có thể kiểm thử riêng |
| `WEB_AUTO_FETCH_MAX_SOURCES` | `2` | Tối đa số website được tự động đọc |
| `WEB_AUTO_FETCH_BUDGET_SEC` | `20` | Tổng ngân sách thời gian auto-fetch trong một câu hỏi |

Không thêm cấu hình mới cho 6.000/tool và 12.000/synthesis cho đến khi có benchmark chứng minh cần tăng. Sử dụng Pydantic validator và quy tắc đồng bộ `.env` hiện có; không tự tạo alias env ngoài schema.

## 3. Kế hoạch triển khai theo bốn PR

### PR1 — Baseline và đánh giá có thể tái lập [x]

1. Xây bộ đánh giá offline ban đầu **40 câu**: 10 fact lookup, 8 buried evidence, 8 multi-source, 7 fresh/time-sensitive, 7 unknown/conflicting. Mỗi case có query, yêu cầu thời gian nếu có, URL hoặc snapshot fixture hợp lệ, expected evidence, đáp án chuẩn/tiêu chí đánh giá, danh sách nguồn được phép và trạng thái không chắc chắn mong đợi.
2. Thêm phép đo tách riêng: `source_recall@8`, `evidence_recall`, `answer_accuracy`, `citation_precision`, `unsupported_claim_rate`, latency từng stage (p50/p95), request token median và số lần fetch. Đánh giá accuracy/citation từ gold labels + kiểm tra thủ công ca tranh chấp; không dùng model tự chấm làm nguồn phán quyết duy nhất.
3. Chạy baseline V1 trên cùng tập, lưu kết quả aggregate có thể tái tạo (không log full nội dung hay dữ liệu riêng tư). Live network smoke chỉ là bài kiểm thử bổ sung; offline fixtures làm chuẩn regression.
4. Đảm bảo file plan này được **track và commit trong Git trước khi triển khai các PR tiếp theo**, để sau khi xóa vẫn đọc được trong lịch sử.

**Gate PR1:** suite chạy ổn định, có số đo baseline cho mọi metric, snapshot/fixtures không chứa secret hoặc nội dung thu thập trái phép.

### PR2 — Lấy chứng cứ nằm sâu trong trang [x]

1. Thêm pipeline extraction/chunking/ranking bên trong reader và URL orchestration, chỉ kích hoạt khi `WEB_EVIDENCE_V2_ENABLED=true`; khi flag off phải giữ hành vi V1.
2. Generic HTML parser cần đọc tiếp sau ký tự 8.000 ở tầng nội bộ, bảo toàn giới hạn byte, timeouts, redirect/SSRF protection và bounded parser buffer. Jina và Crawl4AI được xử lý qua cùng chuẩn evidence; nếu backend đã cắt nội dung trước đó thì metadata `truncated` phản ánh đúng sự kiện.
3. Thực hiện chọn chunk có liên quan và phủ các section quan trọng với bài toán tóm tắt dài; loại chunk trùng/boilerplate, giữ URL/heading, cho phép thấy chứng cứ ở vị trí ~25.000–100.000 khi dữ liệu đầu vào có.
4. Cắt evidence **trước** hard cap 6.000 ký tự/tool; đồng thời bảo vệ `app/ai/router.py` khỏi cắt mất nhãn/URL khi output dài bất thường. Không xóa các giới hạn bảo vệ cuối đường truyền.

**Gate PR2:** test chứng minh câu trả lời nằm sau 8.000 ký tự vẫn có trong evidence; test trang ngắn, HTML lỗi, nhiều section, website chỉ có navigation, tóm tắt toàn trang, nguồn không đọc được; không lỗi SSRF/timeout/cancellation và regressions reader/Crawl4AI/X.

### PR3 — Ranking nguồn và auto-fetch có điều kiện [x]

1. Giữ `MAX_RESULTS=8`, dedupe bằng URL canonicalization hiện có; xếp hạng web dựa trên normalized query ↔ title/snippet, trọng số title 2 và snippet 1, rồi đa dạng domain. Không áp dụng logic này cho image search.
2. Với `SEARCH_BACKEND=auto`, chỉ dừng ở SearXNG nếu có **ít nhất hai kết quả web từ hai domain khác nhau có điểm lexical >=0,20**; nếu không thì gọi DDGS top-up trong search timeout hiện có, merge/dedupe/rank lại. Thiếu date đáng tin cậy thì không suy diễn độ mới của bài viết.
3. Khi `WEB_EVIDENCE_V2_ENABLED` và `WEB_AUTO_FETCH_ENABLED` đều bật, tự fetch tối đa 2 nguồn cao điểm từ search khi câu hỏi đòi hỏi chứng cứ website và snippet chưa đủ. Dùng song song tối đa 2 tác vụ trong **tổng budget 20 giây** và không vượt budget/cancellation của question/job; user đưa URL thì ưu tiên URL đó, không auto-fetch thêm trừ khi nội dung không đủ và câu hỏi cần đối chứng.
4. Nếu fetch fail/hết budget, trả phần evidence hợp lệ đã có kèm trạng thái thiếu chứng cứ; không tạo URL hoặc khẳng định trang đã đọc. Cache nội dung theo request và canonical URL để tránh fetch lại khi model gọi `fetch_url` cùng trang.

**Gate PR3:** kiểm thử SearXNG 2 kết quả kém chất lượng, top-up DDGS, trùng domain/URL, backend lỗi, stale cache, cancellation, multi-user controlled jobs, URL do user nhập và trường hợp không cần web; so sánh độ trễ so với baseline.

### PR4 — Synthesis, benchmark mở rộng và rollout [x] (code/benchmark/docs offline; rollout chưa)

1. Thay cơ chế chia đều ngân sách synthesis bằng evidence packing ưu tiên đoạn có độ liên quan cao, tối ưu số nguồn/độ phủ và giữ nhãn URL/chunk. Tiếp tục giới hạn tổng 12.000 ký tự. Các tool không thuộc web vẫn phải hoạt động và không bị bỏ nội dung chỉ vì thiếu evidence metadata.
2. Mở rộng test set lên **100 câu**: 30 fact, 20 multi-source, 20 buried, 15 fresh, 15 unknown/conflicting. So sánh cùng dữ liệu và cấu hình model trên ba nhánh **V1 / chỉ tăng ký tự / adaptive evidence V2** để xác định hiệu quả thật của mỗi cải tiến.
3. Cập nhật `docs/SEARCH_RESILIENCE.md`, `docs/CRAWL4AI_INTEGRATION.md`, `docs/README.md`, `README.md` và `.env.example` ở chỗ thay đổi runtime/config; không giữ tài liệu mô tả thông số đã bị thay thế.
4. Phát hành feature flag theo 5% → 25% → 100% luồng phù hợp, ghi nhận metrics aggregate và rollback ngay nếu tăng lỗi bảo mật, tăng unsupported claims hoặc vượt ngân sách định sẵn. Sau khi đạt 100%, quan sát tối thiểu **7 ngày** trước khi kết luận hoàn tất.

**Gate PR4:** toàn bộ acceptance ở mục 4 đạt trên test set và rollout thực tế, không còn regression, tài liệu hiện hành khớp code, rollback đã được thử bằng cách tắt feature flag.

## 4. Acceptance và nguyên tắc đánh giá

| Thước đo | Ngưỡng mục tiêu so với V1 |
| --- | --- |
| Answer accuracy | Tăng **>=5 điểm phần trăm** |
| Evidence recall | Tăng **>=10 điểm phần trăm** |
| Citation precision | **>=95%** |
| Unsupported claim rate | Không tăng |
| Latency p95 | Không tăng quá **25%** |
| Median tokens/request | Không tăng quá **30%** |
| Regression tests hiện có | Tất cả pass |
| Lỗi bảo mật mới | **0** |

Đây là **mục tiêu**, chưa phải kết quả đã đo. Bộ 100 câu có sai số đáng kể; nếu các con số sát ngưỡng, phải mở rộng sample hoặc kiểm tra thủ công trước khi quyết định rollout. Dữ liệu fresh phải được ghi nhận thời điểm để tránh so sánh kết quả cũ với một nguồn đã thay đổi. Phân tích nguyên nhân riêng cho `search retrieval`, `fetch/extraction`, `evidence selection` và `synthesis` nếu không đạt gate; không tự động tăng context để che lỗi retrieval.

## 5. Các kiểm thử bắt buộc và failure modes

- **Unit:** tokenization tiếng Việt có/không dấu, phrase matches, URL canonicalization, chunk boundaries/overlap, heading attribution, duplicate section, evidence packing có metadata và tổng ký tự.
- **Reader:** bài viết 200 ký tự, bài 100.000 ký tự có đáp án ở giữa/cuối, Jina navigation đầu trang, HTML hỏng, nhiều heading, media-only, charset không hợp lệ, response vượt 2 MiB, timeout và SSRF/redirect/DNS rebinding.
- **Search:** SearXNG tốt/kém, DDGS fallback hoặc top-up, một domain lặp, cả hai backend chết, stale cache, singleflight, ảnh và X giữ semantics cũ.
- **Model/tool:** tool output >6k, synthesis nhiều nguồn trong 12k, URL source attribution đúng, không trả lời chắc chắn nếu thiếu evidence, không thực thi prompt injection trong nội dung trang, tóm tắt tài liệu nhiều section.
- **Operations:** parent cancellation hủy child tasks; deadline của controlled job/auto-fetch không bị vượt; feature flag rollback V1; logs không chứa full fetched text, token hoặc thông tin nhạy cảm.
- **Regression:** ít nhất chạy các test hiện có quanh `test_reader_article_focus.py`, `test_search_resilience.py`, `test_crawl4ai_client.py`, `test_fresh_synthesis_routing.py`, `test_url_tool_integration.py`, cùng toàn bộ suite khi kết thúc mỗi PR.

## 6. Quy tắc an toàn khi thực hiện

- Đọc lại code/AGENTS.md tại `HEAD`, kiểm tra GitNexus index và cập nhật index nếu stale. **Bắt buộc chạy impact upstream trước khi sửa symbol**, đặc biệt `_execute_tool_batch` và `_compact_tool_outputs` từng cho kết quả **HIGH**; phân tích blast radius/callers và không đọc `UNKNOWN` là an toàn.
- Chạy `detect-changes --scope all` trước commit để kiểm tra ảnh hưởng; kiểm tra thủ công các call path mà graph không thấy. Tránh phá source policy, cache, breaker, timeout, X resolver, Crawl4AI isolation và Telegram UX.
- Mỗi PR có test/benchmark chứng minh đúng gate của giai đoạn; không lẫn thay đổi feature với dọn code ngoài phạm vi. Không thêm thư viện nặng khi lexical pipeline đủ đáp ứng.
- Ghi lại trong PR kết quả đo thực tế, giới hạn còn tồn tại và hướng rollback. Không tự nhận đã đạt acceptance nếu chưa chạy đo và kiểm chứng.

## 7. Theo dõi tiến độ

- [x] PR1: Baseline 40 cases, metrics + file plan đã commit
- [x] PR2: Extraction/chunking/evidence selection, có chứng cứ sâu trong trang (offline; chưa rollout)
- [x] PR3: Source ranking, quality top-up, conditional auto-fetch (offline; flag OFF mặc định, chưa rollout)
- [x] PR4 (offline): Synthesis packing, 100-case benchmark, docs — rollout 5/25/100% và quan sát 7 ngày **chưa** thực hiện
- [ ] Toàn bộ unit/integration/security/regression tests pass
- [ ] Các mục tiêu chất lượng/token/latency ở mục 4 đã được đo và đạt
- [ ] Rollout 100% ổn định ít nhất 7 ngày, có ghi nhận kiểm tra rollback
- [ ] Code và docs vận hành đã cập nhật; không còn blocker hoặc PR dở
- [x] File này đã từng tồn tại trong Git commit và có thể xem bằng `git log -- docs/WEB_EVIDENCE_V2_IMPLEMENTATION_PLAN.md` (commit `5f93dc8`)
- [ ] Cleanup commit cuối: xóa file này khỏi working tree sau khi xác nhận các điều kiện trên

**Cập nhật checklist theo từng PR** ngay trong file để chat mới hoặc agent khác có thể tiếp tục. Nếu không đạt metric, giữ file/flag, ghi rõ blocker và tiếp tục điều chỉnh; **không xóa chỉ vì đã viết xong code**.

## 8. Quy trình xóa file sau khi hoàn thành

1. Kiểm tra mọi mục ở mục 7 trừ dòng cleanup đã hoàn tất và có bằng chứng trong test/benchmark/rollout report hoặc PR history.
2. Xác minh file từng được commit (`git log -- docs/WEB_EVIDENCE_V2_IMPLEMENTATION_PLAN.md`) và kế hoạch có thể khôi phục từ lịch sử. Nếu chưa được track, commit file trước rồi mới cleanup ở commit sau.
3. Chỉ khi toàn bộ PR đã merge và rollout 100% qua tối thiểu 7 ngày không regression, xóa **duy nhất** `docs/WEB_EVIDENCE_V2_IMPLEMENTATION_PLAN.md`; kiểm tra không có đường dẫn tham chiếu đến file kế hoạch trong tài liệu/code còn sống.
4. Chạy các kiểm tra cần thiết và GitNexus `detect-changes` trước cleanup commit; commit thông điệp gợi ý: `docs: archive completed web evidence v2 plan`.
5. Dùng `git show <commit-chua-plan>:docs/WEB_EVIDENCE_V2_IMPLEMENTATION_PLAN.md` để tra cứu trong tương lai. Không tự động xóa khi vẫn còn checklist chưa đạt hoặc chưa có bằng chứng về thời gian quan sát rollout.

> **Lưu ý cho chat triển khai mới:** Đây là specification tham chiếu. Nếu source hiện tại khác với snapshot trong mục 1, điều chỉnh cách hiện thực phù hợp thực tế và ghi lại lý do, nhưng giữ mục tiêu, giới hạn an toàn và acceptance. Chỉ công bố hoàn thành khi có kết quả kiểm thử và đo đạc thực tế.

### Nhật ký triển khai (2026-10-14)

- PR1: 40 synthetic offline snapshots đã chạy; 6 benchmark tests pass. V1 source recall@8 = 0,8627; evidence recall = 0,4894; extractive accuracy proxy = 0,4750; citation precision proxy = 1,0000; unsupported claims proxy = 0,0000; trung bình 2 fetch/case. Token 728 median (estimate). Chi tiết latency và SHA-256 trong `docs/benchmarks/web_evidence_v1_baseline.json`; cách diễn giải và giới hạn trong `docs/benchmarks/README.md`.
- PR2: Đã đưa extraction HTML/Jina/Crawl4AI và selection chunk sau feature flag OFF mặc định; giữ URL/X precedence, 2 MiB tải về, SSRF/redirect/DNS pinning, cancellation, giới hạn 6k/tool và 12k/synthesis. Test sâu >25k ký tự, HTML hỏng, trang chỉ có navigation (trả `ok=false`), fallback, V1 rollback. Benchmark 40 cases V2: source recall@8 0,8627; evidence recall 0,6383 (**+14,89 điểm phần trăm**); accuracy **extractive proxy** 0,6500 (**+17,50 điểm phần trăm**); citation proxy 1,0000; unsupported proxy 0; median token estimate 782 (+7,4%); p95 offline 632,316 ms (V1 764,749 ms, thời gian dao động theo máy); 2 fetch/case, 0 network. `python -m unittest tests.test_web_evidence_v2 tests.test_reader_article_focus tests.test_url_service tests.test_tool_url_dedupe -q`: 27 OK; full suite sau bản sửa cuối: 659 OK, 11 skipped; Ruff pass. Report trong `docs/benchmarks/web_evidence_v2_pr2.json`.
- PR3: thêm `app/search/source_ranking.py` (lexical score title×2/snippet×1, canonical dedupe, domain diversity, gate >=2 domain >=0,20) và `app/search/auto_fetch.py` (chọn target, budget question-scoped, pack evidence trong cap tool). `app/search/router.py` dùng quality gate khi `WEB_EVIDENCE_V2_ENABLED=1` và tách cache key `:v2`; `app/core/orchestrator.py` auto-fetch tối đa 2 nguồn, song song <=2, budget 20s/câu hỏi, chặn bởi budget question/job, bỏ qua khi user đã đưa URL, cache theo canonical URL trong request (chia sẻ với `fetch_url`), báo rõ nguồn không đọc được. Image search và X resolver không đổi; budget/SSRF/2 MiB/6k/12k giữ nguyên.
  Test: `tests/test_web_evidence_source_ranking.py` (20) và `tests/test_web_auto_fetch.py` (16) bao phủ ranking, gate, top-up, flag rollback, image không đổi, cache tách V1/V2, cancellation, duplicate URL/domain, budget, concurrency và 2 câu hỏi đồng thời. `python tests/run_tests.py`: **695 OK, 11 skipped**; `python -m ruff check .`: pass. Benchmark 40 case (fixture SHA-256 không đổi `1bf0e345…c6323d`): evidence recall **0,7234** (V1 0,4894; +23,40 điểm phần trăm), accuracy **extractive proxy** 0,6750 (+20,00 điểm phần trăm), source recall@8 0,8627, citation/unsupported proxy không đổi, token median estimate 783,5 (+7,6%), p95 tổng offline 587,2 ms (V1 baseline đã commit: 764,7 ms; PR2: 632,3 ms — số theo máy). Report: `docs/benchmarks/web_evidence_v2_pr3.json`. Vẫn là proxy offline, không phải chất lượng model thật.
  GitNexus: `impact --direction upstream` cho `route_auto` và `_format_search_results` là **HIGH** (mỗi symbol 2 direct dependents, 3–4 process, 3 module) — đã kiểm thử reader/navigation/SSRF/cancellation/url-dedupe/search-resilience để chặn hồi quy; `read_url` và `ask` trả `UNKNOWN` khi tra theo tên nên đã xác minh bằng test hiện có thay vì coi là an toàn.
- Bàn giao triển khai kế tiếp: xem `docs/WEB_EVIDENCE_V2_CHECKPOINT.md` và `docs/WEB_EVIDENCE_V2_NEXT_MODEL_PROMPT.md`. Cần tiếp tục PR4, chạy benchmark/test theo gate; không đánh dấu các mục nghiệm thu tổng thể chỉ từ proxy offline.
- PR4: `app/ai/synthesis.py` thay chia đều ngân sách bằng packing theo relevance + đa dạng nguồn: tách block theo label `[SOURCE n / CHUNK m]`/`[AUTO SOURCE]`, giữ URL/Heading/trạng thái đọc, xếp theo lexical relevance của câu hỏi rồi round-robin nguồn, query không có term thì giữ thứ tự tài liệu, output không thuộc web giữ thứ tự và phần ngân sách riêng. Cap 6.000/tool và 12.000/synthesis **không đổi**; nội dung trang vẫn nằm trong khung dữ liệu không đáng tin.
  Fixture mở rộng lên **100 case** (`tests/fixtures/web_evidence_v2/cases_100.json`, SHA-256 `51e189382cad7efaeb245a75d9b11e9d1bb73e4077c1ac746b02f05012d9e399`; 30 fact, 20 multi-source, 20 buried, 15 fresh, 15 unknown/conflicting) và giữ nguyên file 40 case (`1bf0e345…c6323d`, byte-identical). Benchmark replay ba nhánh trên cùng dữ liệu: `--config v1|chars|v2` (chars = chỉ nâng giới hạn ký tự 32.000/32.000, là cấu hình mô phỏng so sánh, không phải config ship).
  Kết quả 100 case (report `docs/benchmarks/web_evidence_100_{v1,chars,v2}.json`): evidence recall fetched 0,4790 / 0,6134 / **0,6891**; evidence recall trong context model nhận 0,4790 / 0,4790 / **0,6891**; labeled excerpt 0 / 0 / 169; extractive accuracy proxy 0,46 / 0,62 / **0,63**; citation & unsupported proxy không đổi (1,0 và 0,0 — do thiết kế judge); token ước lượng median 744,5 / 744,5 / 794,0 (+6,6%), mean 897,8 / 1164,5 / 995,5, p90 1710 / 3377 / 2081; latency p95 offline 800,7 / 773,6 / 786,2 ms; 2 fetch/case; non-web tool output được giữ 100%. Nhận định: nâng giới hạn ký tự một mình lấy được đoạn sâu ở tầng fetch nhưng packer chia đều lại cắt mất khi gửi model; V2 vừa lấy được chứng cứ vừa giữ label trong cap 12k. Tất cả vẫn là proxy offline.
  Test: `tests/test_synthesis_evidence_packing.py` (11) và bổ sung cho `tests/test_web_evidence_benchmark.py` (11) gồm determinism ba nhánh, fixture 100 case, delivery khác nhau của buried case, packing theo relevance/diversity, giữ label/URL, không bỏ non-web tool output, cap không vượt, prompt injection ở trong khung untrusted. `python tests/run_tests.py`: **713 OK, 11 skipped**; `python -m ruff check .`: pass.
  GitNexus: `build_fresh_synthesis_messages` upstream = **CRITICAL** (26 impacted, 2 direct là các call site trong `app/ai/router.py`, 41 process, 10 module); `_compact_tool_outputs` = **CRITICAL** (1 direct, 41 process); `detect-changes --scope all` = critical, 9 file/37 symbol, 121 process, không có cờ partial/truncated. Đã chứng minh bằng test synthesis/fallback/tool-budget hiện có thay vì coi là an toàn.
- Báo cáo nghiệm thu độc lập: `docs/WEB_EVIDENCE_V2_ACCEPTANCE_REPORT.md` (PASS/FAIL/NOT MEASURED cho từng tiêu chí); báo cáo kết quả triển khai sau hardening: [`docs/WEB_EVIDENCE_V2_RESULTS_AND_ACCEPTANCE_REPORT.md`](WEB_EVIDENCE_V2_RESULTS_AND_ACCEPTANCE_REPORT.md), kèm prompt kiểm chứng cho model khác tại [`docs/WEB_EVIDENCE_V2_INDEPENDENT_VERIFICATION_PROMPT.md`](WEB_EVIDENCE_V2_INDEPENDENT_VERIFICATION_PROMPT.md).
- Chưa có đánh giá câu trả lời của model thật, chi phí provider, latency network, hoặc rollout. Những mục đó vẫn là blockers của acceptance PR4 và mục 7.

### Nhật ký hardening V2 (2026-10-14, sau nghiệm thu độc lập)

Sửa các khiếm khuyết đã được kiểm định độc lập; không nới lỏng assertion nào, không tăng
cap 6.000/tool hay 12.000/synthesis, hai flag vẫn mặc định OFF.

- **FIX-01 auto-fetch coordinator:** thay `AutoFetchBudget` trừ-thời-gian-sau-`gather()`
  bằng `app/search/auto_fetch.py::AutoFetchCoordinator` request-scoped (deadline
  monotonic, reservation canonical URL bằng `asyncio.Lock`, semaphore 2). Trước đó hai
  burst `web_search` đồng thời đọc 4 nguồn với peak concurrency 4; sau khi sửa còn 2
  nguồn/peak 2 cho toàn câu hỏi.
- **FIX-02 framing hardening:** `evidence.neutralize_untrusted` vô hiệu hóa delimiter hệ
  thống trong nội dung không đáng tin; áp dụng tại reader/url_service, `select_evidence`,
  `pack_evidence` và search listing, cộng `defuse_frame_markers` trong synthesis. Một
  trang chứa `[/DỮ LIỆU NGHIÊN CỨU]` hay `[SOURCE 1 / CHUNK 9] URL: evil` không còn tạo
  được marker đóng khung giả/nhãn nguồn giả.
- **FIX-03 rollback V1 synthesis:** thêm `ChatRequest.synthesis_mode` (mặc định `v1`) đi
  xuyên `AIProviderRouter` tới `build_fresh_synthesis_messages`; `v1` gọi
  `_compact_tool_outputs_v1` (bản sao byte-for-byte thuật toán trước PR4),
  `v2` dùng packing thích ứng. Không dùng global mutable flag.
- **FIX-04 DDGS candidate bị loại:** `route_auto` merge candidate vào pool sâu hơn
  (`limit * 3`) trước khi rank và cắt còn 8, nên nguồn DDGS chất lượng cao không còn bị
  bỏ khi SearXNG trả về đủ 8 kết quả yếu.
- **FIX-05 output budgeting:** reserve trước rồi mới render search listing; khi có nguồn
  cần đọc, listing <= 3.600 ký tự và bằng chứng auto-fetch được dành tối thiểu 2.400 ký
  tự; không có auto-fetch thì listing dùng tối đa ngân sách còn lại. Tổng <= 6.000/tool.
- **FIX-06 adversarial tests:** `tests/test_web_evidence_v2_hardening.py` (26 test, đo trên HEAD hiện tại) phủ
  burst đồng thời, reservation atomic, budget 0,2s, cancellation không rò task
  (`active_operations == 0`), controlled-job budget/cleanup, delimiter/full-width/Unicode
  injection, frame integrity, parity V1 và isolation nhiều request.
- **FIX-07 benchmark auto-fetch:** `python -m scripts.web_evidence_benchmark --auto-fetch`
  chạy `Orchestrator.ask` qua mock router/reader và ghi
  `docs/benchmarks/web_evidence_auto_fetch.json` (network reads, số nguồn, concurrency,
  read success, evidence delivered, output trong cap). Báo cáo cũ giữ
  `conditional_auto_fetch` trong `not_measured`.
- **FIX-08 chênh lệch token 728→743:** điều tra xác định đây là **artifact của harness
  benchmark**, không phải hồi quy production: PR4 thêm output non-web thứ tư vào context
  nên phép chia đều V1 đổi share. `--legacy-harness` (3 output) tái lập chính xác
  baseline đã commit (median 728.0, evidence recall 0.4894, accuracy proxy 0.475);
  `_compact_tool_outputs_v1` được chứng minh byte-for-byte. Test ghi cả hai median.
- **FIX-02 residual (kiểm tra lại luồng thật ngày 2026-10-14):** khung/nhãn giả vẫn lọt
  khi trang chèn dấu phân cách vào giữa cụm từ dành riêng hoặc xuống dòng trước ngoặc
  đóng — `[/DỮ-LIỆU NGHIÊN CỨU]`, `[/DỮ.LIỆU.NGHIÊN.CỨU]`, `[/DỮ LIỆU NGHIÊN CỨU\n]`,
  `[SOURCE-1/CHUNK-9] URL: evil`, `[SOURCE 6 / CHUNK 8\n] URL: evil`,
  `[AUTO-SOURCE] URL: evil` — cùng biến thể dấu ngoặc full-width. Các marker này sống sót
  suốt `select_evidence → pack_evidence → build_fresh_synthesis_messages` và tới thẳng
  prompt tổng hợp (repro bằng harness adversarial trên luồng thật, không chỉ từng hàm).
  Sửa tối thiểu tại ranh giới tin cậy trong `app/search/evidence.py`: thêm `_SEP`/`_CLOSE`
  cho phép dấu phân cách (khoảng trắng, gạch ngang/en–em dash, dấu chấm, gạch chéo, NBSP)
  giữa các từ dành riêng và cho phép khoảng trắng/xuống dòng trước ngoặc đóng. Nới rộng
  chỉ **tăng** khả năng vô hiệu hóa look-alike, không xóa bằng chứng hợp lệ; tên hàm công
  khai không đổi. Trước → sau: 6/6 biến thể thoát khung trước khi sửa → 0/6 sau khi sửa;
  regression `ReservedMarkerLookalikeTests` (3 test) chạy qua cả ba tầng, cộng 1 test
  khẳng định nhãn nguồn thật do renderer tạo vẫn còn trong `pack_evidence`.

- Trạng thái sau hardening: `python tests/run_tests.py` và `ruff check .` pass; benchmark
  40-case V1 legacy-harness trùng baseline lịch sử; auto-fetch replay thoả invariant
  `max_two_sources`, `max_two_concurrent_reads`, `output_within_cap`. Các mục model thật,
  human citation, latency network, provider token và rollout **vẫn NOT MEASURED**.
