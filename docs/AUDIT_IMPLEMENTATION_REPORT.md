# Audit remediation implementation and VPS handoff — 2026-10-11

> **Status:** completed local AUD-01..AUD-05 remediations, independent
> repository audit performed; remaining items and unexecuted gates below.
> Branch: `codex/audit-remediation-2026-10-11`; pre-change HEAD:
> `8e3f691709f58567b36af3b791364d2c334f37e6`. This is the pre-fix
> baseline, not a release image containing the fixes. Release SHA and remote
> deployment validation must be recorded separately.

## Changed implementation and compatibility

| Workstream | Files | Change and regression protection |
| --- | --- | --- |
| AUD-01 terminal multipart state | `app/bot/question_runner.py`, `app/core/job_control.py`, `app/core/job_manager.py`, `app/bot/job_status.py`, `app/core/stats.py` | Introduced `PARTIALLY_DELIVERED` terminal state and exact confirmed `sent_parts/total_parts`; commits acknowledged prefix plus incomplete marker, independent partial metric and no replay. Protected in `tests/test_audit_delivery_regressions.py`, `tests/test_question_delivery.py`, `tests/test_job_router.py`, manager/status/terminal/callback/history suites. |
| AUD-02 tool error isolation | `app/ai/router.py` | Bounded provider-visible stable error categories; preserved tool concurrency/cancellation. Privacy regressions in `tests/test_audit_tool_error_privacy.py`. |
| Supplemental NEW-03 provider diagnostic isolation | `app/ai/drivers/openai_chat.py` | Unknown AI-returned tool names no longer enter provider exceptions, router warning logs or health `last_error`. Synthetic newline/sentinel regression in `tests/test_pr58_provider_payload_exception_privacy.py`; FAIL before fix, PASS after. |
| AUD-03 diagnostics portability | `scripts/check_markdown_links.py` | Display paths use forward slashes; filesystem resolution unchanged. Existing Markdown diagnostic tests pass. |
| AUD-04 limiter retention | `app/core/rate_limiter.py` | **FIXED + VERIFIED locally on 2026-10-12:** expiration min-heap, independent request/warning deadlines, generation tokens and heap compaction replace the 256-key/5-second sweep. The request window is unchanged; warning cooldown is explicitly limited to finite 0–120 seconds. Sustained-churn, input-validation and handler regressions in `tests/test_audit_retention_regressions.py`. |
| AUD-05 memory retention | `app/core/context.py`, `app/config.py`, `app/main.py`, `.env.example`, `README.md` | Defaults `MAX_CONTEXT_CONVERSATIONS=2048`, `CONTEXT_TTL_SEC=86400`; monotonic LRU/TTL, chat/topic isolation and ordering. Memory and existing forum/topic tests pass. |
| Additional privacy defense | `app/search/reader.py`, `app/bot/handlers.py`, `app/bot/image_results.py`, `app/main.py` | Sanitized diagnostic exception data at user/log boundaries; regressions `tests/test_audit_reader_error_privacy.py`, `tests/test_audit_legacy_logging_privacy.py`. |
| Operational docs | `docs/QUESTION_CONTROLS.md`, `docs/TELEGRAM_FORMATTING.md`, `docs/README.md` | Updated terminal and retention explanations. |
| Original tests adjusted | `tests/test_provider_observability.py`, `tests/test_question_delivery.py`, `tests/test_job_router.py` | Expected state/counter updates for deliberate behavior change. |

Five added audit test files are untracked until review/staging; original user
untracked guidance (`AGENTS.md`, `CLAUDE.md`, `.claude/`) was preserved. No
prompt text was added to the repo. AUD-06 was investigated but not patched
without a confirmed vulnerable service behavior.

## AUD-04 sustained-churn correction — 2026-10-12

The earlier bounded sweep (256 user keys every five seconds) could retire at
most 51.2 keys/second. A deterministic 400-second, 100-new-users/second
regression **failed before the correction**, leaving 23,800 request user keys
instead of the 6,000 still within the 60-second window; another request after
61 idle seconds did not clear all inactive keys. The old sweep tests only
asserted incremental visits and therefore missed sustained-churn retention.

`RateLimiter` now uses `heapq` deadlines and lazy invalidation with unique
tokens. Each user has separate request and warning expiration records. Only
accepted requests advance the request deadline to the last accepted
timestamp + 60 seconds; denied spam never prolongs it. Every `allow()` and
`should_warn()` call removes **all** deadlines that have expired, even when no
request is eligible to run. When the heap exceeds
`2 * (live_request_expirations + live_warning_expirations) + 64`, it is rebuilt
from active records. The state and heap remain proportional to active
window/cooldown records (plus the 64-entry additive heap allowance); heap
operations have amortized O(log N) per update. No new dependency, settings,
worker or public method change was added.

Verification after the correction:

- `python -m unittest discover -s tests -p test_audit_retention_regressions.py -v`:
  **15 PASS**, including exact sliding-window boundaries, cooldown variation,
  independent expirations, spam without deadline extension, 40,000-user churn,
  heap compaction, disabled limiter, and legacy/controlled Telegram handler
  warning replies.
- `python tests/run_tests.py` on Windows Python 3.13: **590 tests PASS,
  2 skipped**. `python -m ruff check .`, `python -m compileall -q app tests scripts`
  and `git diff --check`: **PASS**.
- Deterministic no-network benchmark: 40,000 accepted distinct users over
  400 seconds in **0.116s**; 6,000 live request keys and 6,000 heap entries;
  after 61 idle seconds plus one request, **1 key/1 heap entry**. One hot user
  with 20,000 accepted requests in 20 seconds: **0.033s**, one user key,
  20,000 still-valid timestamps, **2 heap entries** (compaction). Denied-only
  burst of 200,000 calls: **0.197s**, one request key/heap entry. These are
  observations, not CI time thresholds; accepted timestamps must remain until
  their own sliding-window expiry.
- GitNexus pre-edit `impact(RateLimiter, upstream)`: **LOW**, affecting the
  imports in `app/bot/handlers.py`, `app/main.py` and indirectly
  `app/bot/controlled_handlers.py`; `impact(allow, upstream)` included legacy
  Telegram entry points. `detect-changes --scope all`: **24 tracked files,
  83 changed symbols, 38 affected flows, overall critical change impact**
  across the *already modified* audit worktree; this is not AUD-04 security
  severity. No partial or truncated result was reported.

**Python 3.12 follow-up verification (2026-10-12):** with user authorization,
the local `fcai-audit-tests-git:local` image was run offline (`--network none`)
against the bind-mounted updated worktree. All **15 AUD-04 retention tests**
and **9 `test_job_operations.py` tests** passed in focused runs. The first
590-test full run recorded one failure in unrelated
`test_nested_context_budget_is_charged_once_per_attempt`: elapsed time
was 20.103 ms against a strict 20 ms threshold. The focused test passed,
and a subsequent full Docker Python 3.12 run **passed all 590 tests with
zero skips** in **28.593s**. This initial timing-sensitive failure is retained
as a potential flaky-test concern; no out-of-scope test was altered.

**Remaining verification limit:** GitHub-hosted Python 3.11/3.12 CI and
production/VPS validation remain unexecuted. **AUD-04 is FIXED + VERIFIED
in both local Windows Python 3.13 and Docker Python 3.12**, with release gates
still outstanding.

### AUD-04 warning cooldown follow-up — 2026-10-12

Merge-readiness review found a same-user variable-cooldown regression: after
`should_warn(42, 10)` at t=100, a different user's request at t=111 could
expire the warning and `should_warn(42, 90)` then returned `True` instead of
`False`. The deterministic regression **failed before this follow-up patch**.

The initial fix retained warning timestamps for at least 120 seconds and
allowed a suppressed warning to extend its expiration when requested with a
cooldown above 120 seconds. That intermediate behavior is superseded by the
following explicit API bound.

### AUD-04 bounded warning cooldown contract — 2026-10-12

`should_warn(user_id, cooldown_sec=30.0)` now accepts finite `int`/`float`
cooldowns **from 0 through 120 seconds inclusive** (excluding `bool`). Other
types raise `TypeError`; values outside this range, `NaN` and infinities raise
`ValueError`. Validation happens before touching request or warning state.
The timestamp is retained for exactly 120 seconds after each emitted warning,
which covers every allowed cooldown change, including 10 seconds followed by
120 seconds. Suppressed warnings never extend retention. At the 120-second
boundary the old warning can safely expire and a new one can be emitted.
Request and warning expirations stay independent and heap compaction is
unchanged. Telegram callers continue to use the 30-second default.

The invalid-input regression **failed before the validation patch** and
passed afterward. **20 focused tests PASS**, including exact 120-second
boundary, invalid input without state mutation, zero cooldown and bounded
heap. Windows Python 3.13: **595 PASS (2 skipped)**; offline Docker Python
3.12: **595 PASS (0 skipped)**. No-network benchmark (observations only):
40,000 request users over 400 seconds -> **6,000 live request keys**, then
**1** after 61 seconds idle; 40,000 warning users -> **12,000 live warning
keys**, then **0** after 122 seconds idle; 20,000 immediate warnings for one
user -> **1 warning key and 2 heap entries**. Ruff, compileall, Markdown links
and `git diff --check` PASS. GitNexus pre-edit impact was LOW, affecting
`app/bot/handlers.py`, `app/main.py`, and indirectly
`app/bot/controlled_handlers.py`. The graph index was refreshed; analyzer
flow-truncation warnings mean an absent flow is not proof of no dependency.
`detect-changes --scope all` found **4 changed files, 18 symbols, LOW risk**
for this patch (0 reported affected processes). The whole audit branch versus
`main` has a **CRITICAL integration impact** (39 files, 187 symbols, 36
reported affected flows) spanning other AUD workstreams; review that wider
diff independently before merging. GitHub-hosted CI and live Telegram/VPS
checks remain separate release gates.

## Validation and remaining engineering risk

The comprehensive evidence matrix is in
[`AUDIT_POST_FIX_REPORT.md`](AUDIT_POST_FIX_REPORT.md). Local Docker Python
3.12 originally completed **582 tests with no failures/skips**; after the
supplemental NEW-03 privacy fix it completed **583 tests with no failures/skips**
against the current bind-mounted worktree. Host Python 3.13.3 completed
**583 tests with 2 skips**. Ruff, compilation, env vocabulary,
Markdown links, Docker Compose staging validation, rebuilt production image
configuration smoke, production-image `pip check` and ephemeral `pip-audit`
passed. Python 3.11 CI did not run. An intentionally offline Docker build
could not fetch PyPI during dependency installation; a normal local build
succeeded. No external production behavior was exercised.
GitNexus MCP `detect_changes(scope="all")` returned 83 changed symbols, 38
affected symbols, 24 modified tracked files and a critical **change-impact**
rating, which is a reason for staged rollout and regression checks rather
than evidence of a critical security finding.

Independent fresh audit found **NEW-01 LOW**: legacy `_ChatLocks` strong
reference dictionary retains inactive chat/topic locks, reproduced with 3,000
keys. It also retains the conditional **AUD-06 / NEW-02 INFO, UNVERIFIED**
Crawl4AI redirect/DNS egress hypothesis: the pinned crawler image and network
isolation test were unavailable locally. See the post-fix report for severity,
line evidence, mitigation and proposed isolation protocol.
The later supplemental review confirmed and fixed **NEW-03 LOW**: model-supplied
unknown tool names previously entered `ProviderError` and downstream diagnostics.
The red/green regression and updated test/graph results are documented in the
post-fix report. The earlier production-style image build predates NEW-03; a
fresh image build is a release gate after review, and no VPS action was taken.

**Compatibility:** `PARTIALLY_DELIVERED` is terminal and should never be
treated as a queued retry. The progress counters count confirmed Telegram
returns, not ambiguous transport sends. Prefix history is intentionally
marked incomplete; users may manually ask again but the bot never replays
known acknowledged parts automatically. `QUESTION_CONTROLS_ENABLED=0`
retains legacy mode. Memory entries can now expire after 24 hours idle and
at 2,048 keys; this is an intentional retention policy change. Partial
metric records partial answers separately from full success/recovery stats.

## Production rollout runbook — **NOT EXECUTED**

1. **Review and record immutable release:** inspect the worktree/diff,
   stage only intended files, run CI Python 3.11/3.12 and the independent
   audit follow-ups, then commit. Record the resulting **new commit SHA**
   and immutable registry image digest. Do not release using the baseline
   `8e3f691` or a mutable `latest` tag. GitNexus MCP `detect_changes` must be
   reviewed before commit, especially high-risk delivery and AI-router flows.
2. **Backup and rollback preparation:** snapshot operator-owned `.env`,
   Compose config, image/digest and any persistent volumes or bot-relevant
   storage using the VPS's approved backup process. Protect secrets and
   restrict backup access; never write tokens into audit logs. Preserve a
   known-good image digest, deployment manifest and one-command rollback
   procedure; check database/state compatibility before rolling back.
3. **Canary/test chat:** deploy the reviewed digest only under separate
   authorization to a controlled test environment/group. First exercise
   `QUESTION_CONTROLS_ENABLED=0`, then explicitly enable controlled mode
   in the test group. Verify one-message success, multipart 1/N failure,
   stop/renew/callback authorization, chat/forum ordering, HTML fallback,
   status cleanup, source/footer/image best-effort behavior, and memory
   retention boundaries. Confirm metrics count a partial delivery exactly
   once without full success counters or duplicate prefix sends.
4. **Optional Crawl4AI:** leave `CRAWL4AI_ENABLED=0` until the separate
   isolated egress/redirect/DNS acceptance protocol passes for pinned
   `unclecode/crawl4ai:0.9.3`. If unsafe behavior is shown, enforce
   pre-request IP/redirect/network controls, then verify permitted public
   fetches and fallback again before enabling the profile.
5. **Observe and revert on regressions:** monitor container health,
   restarts, Telegram send/429/error rate, `PARTIALLY_DELIVERED` counts,
   failure/success/provider recovery counters, RSS and chat/topic key
   cardinality, tool-timeout/redaction and status-cleanup retries. If
   controlled-mode behavior regresses, use `QUESTION_CONTROLS_ENABLED=0`
   and restart under the operator-approved change process; if optional URL
   rendering is risky, use `CRAWL4AI_ENABLED=0`. Redeploy the recorded
   known-good digest and restore only compatible backups as needed.

**Authorization boundary:** none of the VPS actions above, image pushes,
registry publication, bot restarts, webhook changes or production writes
were performed by this local audit. The working branch remains uncommitted
for review.
