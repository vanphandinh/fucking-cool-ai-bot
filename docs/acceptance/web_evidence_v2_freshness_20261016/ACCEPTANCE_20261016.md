# Web Evidence V2 — ACCEPTANCE (2026-10-16, nhánh `codex/v2-risk-hardening`)

Bằng chứng cho lượt build theo [plan](../../WEB_EVIDENCE_V2_PILOT_EXECUTION_PLAN_20261016.md).
Flags vẫn **OFF**: `WEB_EVIDENCE_V2_ENABLED=false`, `WEB_AUTO_FETCH_ENABLED=false`,
`WEB_EVIDENCE_V2_GROUP_IDS` rỗng.

## 1. Commit map (từ `48b90e6`)

| Commit | Nội dung |
| --- | --- |
| `598609d` | plan + FRESHNESS_DIAGNOSIS (Gate G1) |
| `9e86bee` | fix ranker: giữ slot recency theo hit mới nhất đủ liên quan + 1 truy vấn phụ năm hiện tại |
| `7e534e3` | canary theo nhóm chat (`WEB_EVIDENCE_V2_GROUP_IDS`, `Settings.for_chat`, `UserRequest.chat_id`) + log `v2_canary` |
| `9a0d40f` | đo token thật: parse `usage` ở driver → `ChatResponse.usage` → `CompletionResult.usage` → `Answer.usage` + log `ai_usage` + smoke ghi usage/median |
| `56533b4` | `scripts/web_evidence_pilot_audit.py` + runbook canary tracked |
| (commit này) | luật "doc tracked không link artifact untracked" được thi hành bằng code + quyết định số phận tài liệu untracked |

## 2. Gate offline (chạy thật, có exit code)

| Kiểm | Lệnh | Kết quả |
| --- | --- | --- |
| Full suite | `python tests/run_tests.py` | **859 tests OK (skipped=11)**, exit 0 (chạy 2 lượt: trước `7e534e3` và sau khi sửa 2 lỗi gate) |
| Lint | `python -m ruff check .` | `All checks passed!` |
| Markdown links | `python scripts/check_markdown_links.py` | exit 0 (nay gồm cả luật untracked target) |
| Canonical env | `python scripts/check_canonical_env_vocabulary.py` + test trong suite | sạch |
| Benchmark offline V1/V2 | `PYTHONPATH=. python scripts/web_evidence_benchmark.py --config {v1,v2} --fixture tests/fixtures/web_evidence_v2/cases_100.json` | `network_requests=0`, fixture SHA `51e189382cad7efa…` khớp baseline |
| Benchmark auto-fetch | `... --config v2 --auto-fetch ...` | `max_two_sources/max_two_concurrent_reads/output_within_cap = true` |
| **Linux + image** (hình dạng CI: clean clone + lock) | `docker build` → `git clone` trong `python:3.12-slim` → `pip install -r requirements.lock.txt --no-deps` → `pip check` → suite | `pip check` sạch; **917 tests OK (skipped=1)** trên Python 3.12.15 tại HEAD `07bffb3`, exit 0 (lượt đầu ở `d6bcacc`: 905 OK); `check_markdown_links` + `check_canonical_env_vocabulary` pass; artifact `artifacts/linux_image_verification_20261016.txt` |
| Healthcheck trong image | `docker run ... -m app.core.liveness` (chưa có heartbeat) và có heartbeat | exit 1 "no heartbeat" / exit 0 "heartbeat 0.4s old"; `docker compose config --quiet` exit 0 |

Số đo (artifact trong `artifacts/benchmark_*_20261016.txt`):

| Metric | V1 | V2 |
| --- | ---: | ---: |
| evidence_recall | 0,479 | 0,6891 |
| answer_accuracy_extractive_proxy | 0,46 | 0,63 |
| source_recall_at_8 | 0,8031 | 0,8031 |
| request_tokens_median_estimate | 744,5 | 869 |
| citation_precision / unsupported_claim_rate (proxy) | 1,0 / 0,0 | 1,0 / 0,0 |

V2 không đổi so với baseline đã kiểm định (0,6891) ⇒ thay đổi ranker của `9e86bee`
**trung tính** trên fixture. Điều này khớp với phát hiện G1: fixture không có
`published_at`, nên nhánh recency vốn không chạy ở đó; lợi ích chỉ quan sát được
trên pool thật (H1).

## 3. Bản đồ impact / detect_changes (GitNexus)

| Symbol/file | Direction | Kết quả | Ghi chú |
| --- | --- | --- | --- |
| `rank_web_results` | upstream | **HIGH**, 7 symbol / 2 caller trực tiếp (`tool_executor`, `reserve_auto_fetch_targets`) | đã công bố trước khi sửa; test pin "recent nhưng lạc đề không vào top" giữ nguyên |
| `Orchestrator.ask` | upstream | **CRITICAL**, 66 symbol (41 trực tiếp), 6 process, 3 module | không waive; đã chạy full suite + 4 test canary chứng minh V1 byte-for-byte |
| `Settings.for_chat` | upstream | CRITICAL theo process/module, `riskSharedAxes: LOW`, 1 caller trực tiếp | symbol mới, không có caller cũ |
| `parse_response` | upstream | MEDIUM, 12 symbol (5 trực tiếp) | trường `usage` là optional, giữ tương thích |
| `detect_changes` (canary) | `scope=all` | 20 symbol, 58 affected, risk `critical`, không `partial`/`truncated` | |
| `detect_changes` (usage) | `scope=all` | 24 symbol, 38 affected, risk `critical`, không `partial`/`truncated` | |
| `detect_changes` (audit/runbook) | `scope=staged` | 3 changed files, `changed_symbols=0` | **đọc đúng nghĩa**: file mới chưa có trong index ⇒ "0" là *chưa thấy*, không phải "không ảnh hưởng" |
| `detect_changes` (toàn nhánh vs `main`) | `scope=compare --base-ref main` | **45 file, 146 symbol, 99 process, risk `critical`** | đây là con số "volume" của cả nhánh (không phải một symbol nguy hiểm cụ thể); output nguyên văn: `artifacts/detect_changes_compare_main_20261016.txt` |

## 4. NOT EXECUTED / NOT MEASURED (không được đọc là PASS)

| Hạng mục | Vì sao | Lệnh khi có điều kiện |
| --- | --- | --- |
| **Gate G2 live**: bộ 21 câu × 2 lượt, V2 ≥ V1, 0 claim phụ sai, guard error = 0 | cần provider thật + chấm rubric của người | `python -m scripts.web_evidence_live_smoke --mode both --no-auto-fetch` rồi `python scripts/web_evidence_pilot_audit.py <artifact.json>` |
| **Phase 3.5** smoke Telegram thật (text/vision/tool/fallback/input sai/callback) | cần bot đang chạy + nhóm allowlist | theo runbook canary §3–§5 |
| Latency/token production | không có lượt chạy thật trong phiên này | harness nay đã ghi `provider_token_usage` + median; xem `*_summary` trong artifact |
| GitHub Actions CI (trên runner GitHub) | phiên này chỉ chạy Docker Desktop cục bộ | push sẽ kích hoạt job `audit.yml`; hình dạng lệnh đã tái lập cục bộ (mục 2) |
| Crawl4AI | ngoài phạm vi plan (vẫn `BLOCKED`) | — |

## 5. Sai lệch so với plan (có lý do)

1. **Không thêm category `official_fresh`/regenerate fixture.** Regenerate sẽ đổi SHA
   fixture và vô hiệu hoá baseline đang được test pin (`FIXTURE_CATEGORIES`, 40/100
   case). Thay vào đó hành vi mới được pin bằng test trực tiếp:
   `tests/test_web_evidence_source_ranking.py` (reserve newest eligible) và
   `tests/test_web_freshness_query_boost.py` (truy vấn phụ, tối đa 1, chỉ khi verify).
2. Probe tên `freshness_slot_probe.py` (plan ghi `freshness_pool_probe.py`): một file
   chứa cả chế độ tất định (repro defect) và chế độ pool thật.
3. **Phase 2.4** (ưu tiên target chính thức/mới trong auto-fetch) chưa làm — plan xếp sau G2.
4. **Phase 4.2 (xoá branch) hoãn**: `codex/v2-risk-hardening` giờ chính là nhánh chứa 6
   commit của lượt này, không còn "trùng main"; xoá trước khi merge sẽ mất việc. Cần
   quyết định của chủ dự án (merge rồi xoá).

## 6. Số phận tài liệu untracked (Phase 4.1)

Quyết định: các artifact chỉ tồn tại cục bộ **ở lại untracked** (log audit, session
note/patch, thư mục review cũ); mọi tài liệu tracked chỉ được trỏ tới file tracked.
Luật này trước đây chỉ được "nhắc" bằng văn bản (CI #1975) — từ commit này nó được
**thi hành bằng code**: `scripts/check_markdown_links.py` thêm
`find_untracked_link_targets()` và `tests/test_markdown_links.py` thêm test chặn
repo, nên một link trỏ vào file chưa commit sẽ fail ngay trên máy.

Các nhóm untracked tại thời điểm chốt (giữ local, không link từ doc tracked):

| Nhóm | Ví dụ | Phân loại |
| --- | --- | --- |
| `docs/acceptance/production_audit_20261016/*.log` | `baseline_tests.log`, `docker_build.log` | log phiên, chỉ để đối chiếu cục bộ |
| `docs/acceptance/web_evidence_v2_fast_track_20261016/` | `ACCEPTANCE_REPORT.md`, `BENCHMARK_RESULTS.json` | artifact phiên review trước |
| `docs/acceptance/web_evidence_v2_independent_validation_20261016/` | `QUALITY_EVALUATION.md`, `artifacts/*.json`, `probes/*.py` | artifact phiên review độc lập |
| `docs/WEB_EVIDENCE_V2_*_20261015.md`, `…SESSION_20261015.patch` | checkpoint/session notes | note phiên, không phải tài liệu vận hành |
| `.freebuff/` | metadata công cụ | không thuộc repo |

Tài liệu vận hành tương ứng đã được track ở dạng cập nhật:
[runbook canary](../../WEB_EVIDENCE_V2_PILOT_CANARY_RUNBOOK.md) và
[plan](../../WEB_EVIDENCE_V2_PILOT_EXECUTION_PLAN_20261016.md).

## 6b. Phase 4.3 — pin dependency (A07)

- `requirements.lock.txt`: **36 pin `==`** cho đúng closure runtime, sinh từ chính môi trường
  đã chạy suite xanh (không phải từ bản resolve "mới nhất"), tên canonical, có sort.
- Dockerfile cài từ lock (`--no-deps`); CI cài từ lock + `pip check`; matrix thêm **`3.13`**
  (local đã xanh trên 3.13.3 nên đây là mức patch đã kiểm chứng).
- `tests/test_requirements_lock.py` (6 test) chặn: pin không exact, thiếu dependency gốc,
  version không thoả range trong `requirements.txt`, không sort, lẫn tool test-only, và
  **closure thiếu** — chính lỗi này bắt được ở bản nháp đầu: `pydantic-core` bị bỏ sót vì
  metadata ghi `pydantic_core`, khiến `pip check` fail dù cài "thành công".
- Kiểm chứng end-to-end trong venv sạch: `pip install -r requirements.lock.txt --no-deps`
  + `requirements-audit.txt` → `pip check` = `No broken requirements found` → **891 tests OK
  (skipped=11)**, exit 0. Bằng chứng: `artifacts/lock_venv_verification_20261016.txt`.
- Phát sinh: `packaging` là dependency **test-only** mới (dùng cho check version), được pin
  trong `requirements-audit.txt` chứ không vào lock runtime.

## 6c. Phase 4.4 — liveness (A06)

- `app/core/liveness.py`: heartbeat + probe + CLI; `app/main.py` chạy probe sau khi có bot
  và huỷ trong `finally`; `docker-compose.yml` thêm healthcheck cho service `bot`
  (`start_period: 90s`, retries 3); `.env.example` thêm 3 biến `LIVENESS_*`.
- Probe chỉ touch file khi `getMe` thành công → Telegram hỏng hoặc event loop kẹt thì
  container chuyển unhealthy thay vì "Up" im lặng; log lỗi chỉ ghi `exc_type` (không lộ payload). Khi tắt hẳn, file heartbeat được xoá.
- 14 test offline (`tests/test_liveness.py`) + kiểm chứng trong image (mục 2).

## 6d. Phase 4.5 — static type check ở chế độ báo cáo

- `pyrightconfig.json` (basic, Python 3.11, scope `app` + `scripts`) + `pyright_baseline.json`
  ghi mức đo đầu tiên: **55 lỗi / 85 file** (pyright 1.1.414, 2026-10-16).
  Phân bố: `reportArgumentType` 30, `reportOptionalMemberAccess` 11,
  `reportAttributeAccessIssue` 9, `reportCallIssue` 3, `reportAssignmentType` 1,
  `reportOperatorIssue` 1; nặng nhất `app/bot/handlers.py` (16).
- `scripts/check_types.py` chạy pyright rồi **so với baseline**: vượt baseline ⇒ exit 1
  (chặn regression), bằng/thấp hơn ⇒ 0, không có pyright/npx ⇒ exit 2 = NOT EXECUTED
  (không được đọc là PASS). `--write-baseline` là hành động có ý thức.
- CI (`audit.yml`, chỉ nhánh 3.12) chạy bước này với pyright lấy qua `npx` có sẵn trên runner.
- Lưu ý trung thực: con số baseline **phụ thuộc** `pyrightconfig.json` và version pyright
  (lượt đo đầu tiên truyền scope qua CLI cho 71 lỗi); nếu nâng pyright phải đo lại
  có chủ đích, không nới baseline cho tiện.
- `tests/test_type_check_ratchet.py` (12 test) pin đúng các hướng trên (tăng ⇒ fail,
  thiếu tool ⇒ 2, baseline thiếu ⇒ không claim PASS).

## 7. Trạng thái Gate G2

- Phần offline: **ĐẠT** (mục 2), gồm cả lượt Linux/3.12 hình dạng CI.
- Phần live (2 lượt × 21 câu, chấm rubric, guard error = 0): **NOT EXECUTED**.
  ⇒ Gate G2 **chưa đạt**, vì vậy không chat nào được allowlist và không flag nào được bật.

## 8. Tái lập nhanh

```
python tests/run_tests.py
python -m ruff check .
python scripts/check_markdown_links.py
python scripts/check_canonical_env_vocabulary.py
PYTHONPATH=. python scripts/web_evidence_benchmark.py --config v1 --fixture tests/fixtures/web_evidence_v2/cases_100.json
PYTHONPATH=. python scripts/web_evidence_benchmark.py --config v2 --fixture tests/fixtures/web_evidence_v2/cases_100.json
PYTHONPATH=. python scripts/web_evidence_benchmark.py --config v2 --auto-fetch --fixture tests/fixtures/web_evidence_v2/cases_100.json
python -m scripts.web_evidence_live_smoke --mode both      # cần provider thật
python scripts/web_evidence_pilot_audit.py <artifact.json>

# lock: venv sạch, cài từ lock, suite
python -m venv .venv-lock
./.venv-lock/Scripts/python -m pip install -r requirements.lock.txt --no-deps -r requirements-audit.txt
./.venv-lock/Scripts/python -m pip check
./.venv-lock/Scripts/python tests/run_tests.py
```
