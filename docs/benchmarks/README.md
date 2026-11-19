# Web Evidence V2 offline benchmark

Bộ replay offline dùng fixture synthetic trong `tests/fixtures/web_evidence_v2/`.
Không có website thật, dữ liệu cá nhân hay network request trong replay.

## Chạy

```sh
python -m unittest tests.test_web_evidence_benchmark -v
# 40-case replay set
python -m scripts.web_evidence_benchmark --config v2 --output /tmp/bench_v2_40.json
# 100-case replay set
python -m scripts.web_evidence_benchmark --config v2 \
  --fixture tests/fixtures/web_evidence_v2/cases_100.json --output /tmp/bench_v2_100.json
# Auto-fetch path
python -m scripts.web_evidence_benchmark --auto-fetch --output /tmp/bench_autofetch.json
python -m scripts.generate_web_evidence_fixtures
```

`--config v2` là config duy nhất được hỗ trợ: packer V2 relevance/diversity.
Một fresh 40-case run khớp semantics của `web_evidence_100_v2.json`.

## Fixture

| Fixture | Cases | Category counts | SHA-256 |
| --- | ---: | --- | --- |
| `cases.json` | 40 | 10 fact, 8 buried, 8 multi-source, 7 fresh, 7 unknown/conflicting | `1bf0e34571ea0360d06a348c8d930089e8174b5b60318a37060dab0c75c6323d` |
| `cases_100.json` | 100 | 30 fact, 20 buried, 20 multi-source, 15 fresh, 15 unknown/conflicting | `51e189382cad7efaeb245a75d9b11e9d1bb73e4077c1ac746b02f05012d9e399` |

Case 40-case được sinh từ các entry đầu của từng nhóm, nên vẫn byte-identical
khi 100-case file tăng. Cả hai file chỉ chứa snapshot synthetic `*.example.*`.

## Đọc kết quả

Replay intercept cả hai search backend và URL HTTP reads, sau đó chạy
search routing, URL service, generic reader và synthesis packing thật. Policy
là **cố định**: tối đa 8 search results và đọc hai URL đầu tiên. User thật chọn
tool/URL khác, nên đây là proxy chứ không phải hành vi production.

- Latency là thời gian xử lý fixture cục bộ; p50/p95 tính trên các case, không
  dự phóng network/provider latency.
- Token metric là ước lượng `characters / 4`, không phải provider usage.
- `source_recall_at_8` đo URL nguồn được label nằm trong search results;
  `evidence_recall` đo evidence literal nằm trong output đọc được. Hai metric
  khác nhau: URL tìm thấy có thể không mang evidence.
- `answer_accuracy_extractive_proxy`, `citation_precision_extractive_proxy` và
  `unsupported_claim_rate_extractive_proxy` dùng extractor synthetic, không
  phải model answer. Điểm cao không chứng minh chất lượng model. Các proxy
  metric này không phải rollout acceptance criteria; release decision và human
  review nằm ở
  [`RELEASE_DECISION.md`](../acceptance/web_evidence_v2_independent_validation_20261016/RELEASE_DECISION.md).
- `fixture_sha256` định danh dataset. Các lần chạy lặp phải khớp case results
  và non-timing metrics; wall-clock latency có thể đổi.
- Benchmark phải chạy với network requests bằng **0**.

## Auto-fetch replay

`--auto-fetch` là replay riêng: thay vì policy đọc hai URL đầu, nó chạy
`Orchestrator.ask` thật với mock router, mock search backend và
`reader._fetch_limited` được intercept. V2 policy luôn bật, nên request-scoped
auto-fetch coordinator, URL service, reader và packing đều chạy thật.

Report: `docs/benchmarks/web_evidence_auto_fetch.json`.

| Measurement (40 cases) | Value |
| --- | ---: |
| Mock page-read attempts (`network_requests`; real HTTP = 0) | 162 |
| Cases with an auto-fetch block | 23 / 40 |
| Auto-fetch attempts, mean / max | 2.025 / 4 |
| Auto-fetch sources actually read, mean / max | 0.575 / 2 |
| Peak read concurrency | 2 |
| Read success rate | 0.9 |
| Evidence delivery rate | 0.4681 |
| Tool output within 6,000 characters | 1.0 |
| Estimated request tokens, median | 526.0 |
| Offline elapsed p50 / p95 | 576.17 / 1107.73 ms |

`network_requests` trong JSON này đếm **mock reader calls**, không phải HTTP
thật. Report mang machine-checked invariants: `max_two_sources`,
`attempts_within_ceiling`, `max_two_concurrent_reads`, `output_within_cap`.
Các số evidence vẫn là proxy/offline: không gọi model, không đo live latency.

`auto_fetch_attempts` đếm URL riêng biệt mà burst thử đọc;
`auto_fetch_sources_read` chỉ đếm attempt trả về nội dung. Failed read được thay
bằng candidate kế tiếp trong cùng deadline và claim set, tối đa gấp đôi source
limit, nên attempts không vượt 4.

## Historical records

Các so sánh V1, chars-only, PR2/PR3/PR4, token investigation và archive JSON
trước cutover V2 nằm trong Git history. Chúng không còn runnable và không phải
current gate.
