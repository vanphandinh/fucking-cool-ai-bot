# Kiểm toán Production Readiness — 2026-10-16

## A. Executive summary

- Nhánh: `codex/web-evidence-v2`.
- HEAD ban đầu: `4c0629f4bdd7b497f3a4dc4fa76a2adb78c82067` (không đổi).
- Working tree đã có 17 tracked files sửa và nhiều untracked artifacts của Web Evidence V2 trước phiên này. Không commit, stage, đổi nhánh, đọc secrets, sửa credential hay deploy.
- Phạm vi: kiến trúc và CI/Compose; startup/shutdown; allowlist/admin/callback authorization; job admission, renewal, cancellation, partial delivery; provider protocol/retry/health; RAM retention; URL/SSRF/parser; evidence status/citation; offline regression và production image.
- Đây là kiểm toán theo rủi ro, không phải chứng nhận đã review từng dòng hoặc pentest toàn hệ thống. Kết quả offline không chứng minh Telegram/provider/VPS live đang hoạt động.
- Đã sửa 3 nhóm lỗi thực tế: cleanup bị ngắt giữa chừng; lỗi đọc section bị đánh dấu nguồn thành công; giới hạn buffer HTML không tương thích Python 3.12.15.
- Final GitNexus `detect_changes(scope=all)`: 133 symbols, 52 affected processes, 21 tracked changed files, risk **CRITICAL**, gồm thay đổi có sẵn. Không phải all-clear; tool không báo partial/truncated. Untracked tests được kiểm chứng bằng test runner, không suy diễn là graph đã bao phủ.
- [Change manifest](CHANGE_MANIFEST.json) ghi ownership và SHA256 của 5 source files + 2 test files thuộc audit (reader chỉ một hunk guard).
- **Kết luận: CONDITIONAL GO** cho release có kiểm soát sau các gate mục H; **không cho phép tự động phát hành/bật rộng Web Evidence V2 hoặc Crawl4AI** chỉ từ kết quả này.

## B. Architecture assessment

```text
Telegram long polling (aiogram)
  -> AllowedChat + command/mention/reply filters
  -> legacy serialized conversation handler
     hoặc controlled jobs (capacity + owner/admin callbacks + renewal)
  -> Orchestrator (request-scoped URL cache, tools, evidence)
  -> AIProviderRouter (capability, target/model/credential, retry, scoped health)
  -> openai-chat driver -> Chainnode / xKiro
  -> search router -> SearXNG / DDGS (cache, breaker, singleflight)
  -> URL service -> X resolver / isolated Crawl4AI / Jina + pinned origin reader
  -> Telegram HTML splitting + acknowledged delivery -> ChatMemory + Stats
```

Không có browser frontend, public application HTTP API, SQL database, ORM, migration hoặc external job queue. Telegram là UI; bộ nhớ, stats, health và jobs là process-local RAM. Restart không bảo toàn hội thoại/jobs; nhiều replica không chia sẻ idempotency/rate limit. Đây là giới hạn kiến trúc, không tự ý thêm database.

Điểm mạnh: domain/protocol separation của provider; bounded retries, timeouts, thread work, context TTL/LRU; test fault injection nhiều; network guard offline; SSRF IP pinning và redirect validation; crawler opt-in/private; non-root image; CI action pins.

Điểm cần cải thiện có mục đích: router AI ~815 dòng, orchestrator ~565 dòng, handlers ~557 dòng là các vùng nhiều trách nhiệm. Không rewrite trong phiên này. Luồng legacy/controlled có logic delivery song song: cần giữ contract tests trước khi trích shared seam. Không có process liveness/readiness độc lập hay alert pipeline trong Compose bot; restart policy không phát hiện polling bị treo.

GitNexus index trả đúng HEAD, indexedAt `2026-10-16T06:46:15.869Z`, 270 files/4451 nodes/259 processes. Đã query startup, authorization, SSRF và context main/ask/cleanup/extraction. Context `ask` là lower-bound (một receiver unresolved), không coi graph là đầy đủ. Source verification xác nhận controlled `QuestionProcessor.execute` gọi orchestrator.

## C. Audit findings

| ID | Priority / Type | Module | Problem / Root cause | Impact | Status |
|---|---|---|---|---|---|
| A01 | P1 Confirmed Bug | startup/shutdown | Chuỗi await không bảo vệ cleanup sau khi manager/controls lỗi; registry search clear trước khi một aclose lỗi | Presenter/HTTP clients phía sau không đóng | FIXED, 3 ca red rồi green; thêm cancellation/multiple failures |
| A02 | P1 Confirmed Bug / data integrity | URL evidence | Reader trả thông báo không xác minh được numbered section; success classifier không nhận diện tiền tố này | Đọc thất bại thành `ok=True`, nguy cơ nguồn chứng cứ giả thành công | FIXED, regression red→green |
| A03 | P1 Confirmed Bug / resource guard | V1/V2 HTML parsers | Python 3.12.15 HTMLParser batching giữ thêm `_pending_len`; chỉ đo rawdata bỏ sót >64 KiB | Guard input độc hại không còn giữ contract trên runtime Production | FIXED trong cả hai parser; verified trong production image |
| A04 | P1 Production Blocker / verification | Telegram + AI | Chưa chạy request thật qua Telegram, text/vision/tools/fallback trong phiên này | Chưa chứng minh credential, quota, model và integration live | NOT VERIFIED; cần staging smoke |
| A05 | P1 Production Blocker / verification | Crawl4AI | Không chạy privileged firewall/DNS-rebinding/host reboot thật | Offline shell mocks không chứng minh isolation VPS | Giữ disabled tới khi gate live pass |
| A06 | P2 Architectural Weakness | operations | Bot Compose không có healthcheck; stats/RAM không phải monitoring/alert ngoài process | Polling treo có thể không bị phát hiện; restart mất state | OPEN, thiết kế liveness có tín hiệu thực tế |
| A07 | P2 Potential Risk | dependencies | Requirements có version ranges; base Python tag mutable | Build khác thời điểm có thể thay đổi behavior (A03 là ví dụ thực tế) | OPEN, lock + scheduled updates có đánh giá compatibility |
| A08 | P2 Environment issue | local Python | pip check toàn máy: virtualenv 21.7.9 yêu cầu filelock<4 nhưng có 4.0.0 | Môi trường dev toàn máy không sạch; không phải dependency conflict của image | Không sửa package toàn máy; production image pip check PASS |
| A09 | P2 Release risk | current working tree | Nhiều thay đổi Web Evidence V2 có sẵn chưa commit | Không thể quy toàn bộ diff là patch của audit | Bảo toàn; cần review/ownership trước release |
| A10 | P2 Potential Risk | Linux checkout | Copy CRLF shell từ Windows sang Linux làm `set -o pipefail` lỗi | Deploy từ raw working-tree copy có thể làm security scripts không chạy | Re-run trong disposable Linux copy chuẩn LF; không sửa checkout người dùng |

Nguồn sửa: [main](../../../app/main.py), [search runtime](../../../app/search/runtime.py), [URL service](../../../app/search/url_service.py), [V2 extraction](../../../app/search/evidence.py), [V1 reader](../../../app/search/reader.py).

Không xác nhận thêm lỗ hổng exploit remote/P0 từ bằng chứng phiên này. Không có nghĩa là không tồn tại.

## D. Implemented improvements

1. `_shutdown_question_controls`: `finally` đóng presenter kể cả manager lỗi/cancel.
2. `_amain`: controls cleanup nằm trong try/finally bao phủ Telegram -> Crawl4AI -> search -> providers; giữ thứ tự shutdown.
3. `close_search_runtimes`: thử đóng mọi runtime; một lỗi truyền nguyên kiểu, nhiều lỗi dùng ExceptionGroup. Không nuốt lỗi.
4. `_EMPTY_EVIDENCE`: nhận diện section chưa xác minh, giữ nguyên text/backend nhưng trả `ok=False`.
5. Hai parser tính `len(rawdata) + getattr(_pending_len, 0)` để tương thích stdlib cũ/mới, giữ guard 65536. Dùng private field là compatibility seam cần regression tests khi nâng Python, không disable bảo vệ.
6. Thêm 7 tests trong [shutdown regressions](../../../tests/test_production_shutdown_cleanup.py) và [evidence regressions](../../../tests/test_production_evidence_status.py).

Chỉ thêm một guard expression vào reader vốn đã sửa trước phiên; không thay các hunks Web Evidence V2 của người dùng. Không nâng dependencies, đổi API, đổi flags, tối ưu vi mô hoặc tuyên bố speedup. Không hoàn thiện tính năng mới ngoài sửa tính đúng/reliability của tính năng hiện hữu.

## E. Verification results

Các file `*.log` trong cùng thư mục là evidence chạy local, không đưa vào commit.

| Check | Result | Evidence |
|---|---|---|
| Baseline Windows Python 3.13.3 | PASS có skip: 828 tests / 163.351s, skipped=11 | [baseline](baseline_tests.log) |
| Full suite sau shutdown fix, trước URL/parser fixes cuối | PASS có skip: 833 / 161.932s, skipped=11 | [intermediate full run](final_tests.log); không dùng thay cho final verification |
| Shutdown reproduction | FAIL đúng kỳ vọng: 3 failures | [red](shutdown_red.log) |
| Shutdown regressions | PASS: 5 tests | [green](shutdown_green.log) |
| Evidence status reproduction | FAIL đúng kỳ vọng: True không phải False | [red](evidence_red.log) |
| Final new regressions trong production image | PASS: 7 tests, network none, Python 3.12, actual built app | Terminal run `unittest discover -s /audit-tests -p test_production_*.py` |
| Final Windows targeted V2 suite | PASS: 10 tests; new regression suite 7 PASS | Terminal, sau thay đổi cuối |
| Linux thử đầu | FAIL: 6 failures + 3 errors | [initial Linux run](linux_tests.log); thiếu Git/CRLF fixtures và một regression parser thật; không bỏ qua |
| Linux full suite, harness stage dư untracked docs | FAIL: 835 tests, 1 failure vocabulary / 1 skip | [harness run](linux_final_tests.log); không sửa scanner hoặc bỏ test |
| Final Linux Python 3.12 full suite | PASS: 835 tests / 38.792s, skipped=1, exit=0 | [verified Linux](linux_verified_tests.log); Docker CLI absent trong test container, Compose riêng đã PASS |
| Ruff toàn repo | PASS sau sửa cuối | `python -m ruff check .` |
| compileall | PASS sau sửa cuối | `python -m compileall -q app tests scripts` |
| Type checking | NOT VERIFIED | Repo không cấu hình mypy/pyright; compileall không phải typecheck |
| Canonical env vocabulary + Markdown links | PASS local | Scripts hiện hữu; scanners chủ yếu theo tracked files |
| Compose validation + shell syntax | PASS | `docker compose config --quiet`, `bash -n` sáu security scripts |
| Production build | PASS, BUILD_EXIT=0 | [build](docker_build.log) |
| Production image configuration/non-root | PASS, UID=10001 | `Settings(_env_file=None)`; không nạp credential |
| Image dependency integrity | PASS | `docker run ... python -m pip check` |
| Dependency vulnerability resolution audit | PASS: No known vulnerabilities found | [audit](dependency_audit.log); audit requirements resolution, không phải scan toàn bộ OS/image hay attest mọi installed wheel |
| Local machine dependency integrity | FAIL riêng môi trường | virtualenv/filelock conflict, không tự sửa system |
| Telegram / real LLM / live search / privileged isolation | NOT VERIFIED | Không gửi tin, dùng credential, chạy container đặc quyền hay đổi firewall |

Linux test chạy trên existing local Python 3.12 test image, source copy disposable, network none; không phải full suite trên exact final production dependency set. Production image riêng đã chạy 7 regression mới. Copy cuối chuẩn LF cho `.sh`/`.json`, có Git + temporary index; docs theo đúng [tracked manifest](tracked_docs_manifest.txt), source/tests mới vẫn được include; không có commit, không chạm index checkout thật. Hai lần harness lỗi được giữ làm evidence, không gắn nhãn PASS. Thời gian Windows/Linux khác môi trường, không được diễn giải thành speedup của bản sửa. Các `PASS VPS`/`catalog compatible` trong unit log là mock fixtures, **không phải live production evidence**.

## F. Feature readiness matrix

| Feature | Before | After / Current Status | Dependencies | Remaining Issues | Production readiness | Priority |
|---|---|---|---|---|---|---|
| Telegram group text | Implemented/tested offline | Startup/cleanup hardened | bot token, allowed groups, AI | Live Telegram smoke | Conditional | P1 |
| Provider routing + tools | Extensive offline coverage | Contract retained | Qualified models/keys/endpoint | Live text/tool/fallback | Conditional | P1 |
| Vision input | Bounded download/provider capability | Offline tested, unchanged | Telegram + vision model | Real image understanding | Conditional | P1 |
| Web/image search | Cache/breaker/fallback | Offline regression retained | SearXNG/DDGS network | Live availability/rate limits | Conditional | P1 |
| URL/Jina/X reader | SSRF guarded | False success fixed; parser guard restored | Network/external readers | Live origin/X contracts | Conditional | P1 |
| Web Evidence V2 | Flag-gated, large pre-existing diff | Integrity fixes + tests | Source quality + LLM | Broad diff independent review, live grounding | Not approved for broad enable | P1 |
| Renewable controls | Owner/admin checks, bounded admission | Shutdown failure hardened | Telegram callback/delivery | Live renew/stop, process restart state | Conditional | P1 |
| Multipart/topic memory | Ack-based commit, TTL/LRU | Offline covered, unchanged | Telegram acknowledgments | RAM-only, no restart persistence | Conditional single process | P2 |
| Crawl4AI | Opt-in/private architecture | Offline tests, not live isolated | Linux firewall/DNS/proxy/token | Privileged SSRF and reboot gate | Disabled until verified | P1 |
| CI/build | Python 3.11/3.12 CI declared | Local build + fault tests | CI runners + registry | Python 3.11 not run here; reproducible deps | Conditional | P2 |
| Monitoring/recovery | Logs + in-process stats/restart | No external alert verification | Operator process | Liveness/alert drill/recovery | Partial | P2 |

## G. Remaining technical debt

- P1: live qualification cho exact release artifact và provider configuration; không thể thay bằng mock.
- P1: review riêng toàn bộ Web Evidence V2 hiện tại; graph critical không chứng minh bug nhưng không được coi all-clear.
- P1 nếu bật crawler: Linux isolation, public-sites smoke, DNS rebinding, host reboot và boot policy acceptance.
- P2: dependency lock/SBOM/image digest policy, Python patch-version CI; A03 cho thấy cần test stdlib patch updates.
- P2: chuyển URL read failure classification khỏi localized string sang typed result tại seam phù hợp; hiện sửa tối thiểu để bảo toàn contract.
- P2: readiness/liveness và operational metrics có heartbeat polling; không thêm healthcheck chỉ kiểm tra process tồn tại.
- P2: giới hạn local cleanup chưa bảo vệ repeated cancellation hoặc aclose treo vô hạn; cần timeout policy toàn shutdown trước khi mở rộng.
- P2: single-process RAM state được ghi rõ; durable jobs chỉ nên thêm khi có yêu cầu khôi phục restart/multi replica.

## H. Production release checklist

| Gate | Verdict | Điều kiện |
|---|---|---|
| Giữ branch/HEAD, bảo toàn pre-existing changes | PASS | Không commit/stage/deploy |
| Offline correctness + new regression | PASS có skip | Linux 835 tests, 1 skip; 7 regressions mới PASS trong exact production image |
| Build/config/non-root/image pip check | PASS | Exact final image build |
| Static type checking | NOT VERIFIED | Chưa có toolchain trong repo |
| Secrets hardening | PARTIAL / NOT VERIFIED toàn diện | Không đọc secrets; env không tracked; không chạy full history secret scan |
| Live Telegram text + vision + tools | NOT VERIFIED | Staging allowlisted group, exact image |
| Provider failure + fallback + recovery | NOT VERIFIED live | Controlled test không gây thay đổi production |
| Crawl4AI isolation/reboot | BLOCKED cho enable | Cần môi trường Linux phù hợp và ủy quyền thao tác đặc quyền |
| Web V2 broad enable | BLOCKED | Review diff + live grounding acceptance |
| Monitoring/alert/rollback exercise | NOT VERIFIED | Operator drill |
| Versioned release artifact from owned changes | BLOCKED | Chọn/duyệt working-tree diff trước commit do người dùng yêu cầu |

## I. Final recommendation

**CONDITIONAL GO**, không phải GO ngay. Có thể chuẩn bị release/staging sau khi xác nhận full regression, review diff đang mở và smoke qua Telegram trên exact final image. Nếu yêu cầu bật ngay tất cả optional features hoặc deploy ngay không có các gate trên: **NO-GO cho hành động đó**. Không có production deployment nào được thực hiện.

## J. Next action plan

1. P1: independent review và tách ownership phần Web Evidence V2 vs audit; không stage thay đổi người dùng một cách rộng.
2. P1: chạy staging Telegram text/image/tools/invalid input/forbidden callback/dependency failure/recovery, giữ V2/Crawl4AI tắt mặc định.
3. P1: chỉ bật V2 có canary + grounding acceptance; crawler chỉ sau live isolation/reboot gates.
4. P2: lock dependency và bổ sung Python patch-version compatibility tests; thiết kế typed URL failure seam.
5. P2: heartbeat/alert/rollback drill; quyết định SLA về restart và durable jobs trước khi mở rộng kiến trúc.
