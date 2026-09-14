# PR #48 Independent Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the serious lifecycle/concurrency/observability risks found by the independent audit of PR #48, then re-audit until no reproducible P1/P2 issue remains.

**Architecture:** Keep the renewable-job design intact and make the smallest behavior changes around it. JobControl owns consent timing state, JobManager owns admission/lifecycle enforcement, ChatMemory owns deterministic exchange ordering, JobStatusPresenter owns bounded status-delivery retries, and the Telegram question adapter owns delivery-stage accounting. Every behavior change is introduced with a regression test that is observed failing before production code changes.

**Tech Stack:** Python 3.11/3.12, asyncio, aiogram 3.x, Pydantic settings, unittest, GitHub Actions.

**Spec:** `docs/superpowers/plans/2026-09-14-telegram-renewable-ai-jobs.md`

## Global Constraints

- `QUESTION_CONTROLS_ENABLED=0` remains the default and legacy timeout behavior remains unchanged.
- Controlled jobs remain RAM-only and restart interruption semantics stay explicit.
- Do not replay completed provider/tool/search/URL work after renewal.
- Do not let consent waiting or Telegram status retry consume operation capacity.
- Do not log prompt text, quoted text, image payloads, provider secrets, or answer content at lifecycle boundaries.
- Keep the PR branch `feat/telegram-renewable-ai-jobs`; never modify `main` directly.
- TDD is mandatory: each production behavior change must have a test that failed for the intended reason before the fix.

---

### Task 1: Expire abandoned consent and release admission capacity

**Files:**
- Modify: `app/config.py`
- Modify: `app/core/job_control.py`
- Modify: `app/core/job_manager.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_job_control.py`
- Test: `tests/test_job_manager.py`
- Test: `tests/test_config_regressions.py`

**Interfaces:**
- Produces setting `Settings.question_consent_timeout_sec: float`, env name `QUESTION_CONSENT_TIMEOUT_SEC`, default `900` seconds.
- Produces `JobControl.consent_requested_at: float | None` and `JobControl.consent_timed_out(timeout_sec: float) -> bool`.
- JobManager monitor cancels an `AWAITING_CONSENT` record after the configured timeout and emits terminal cancellation once.

- [ ] **Step 1: Write failing control test**

Add a test that expires a running control into `AWAITING_CONSENT`, advances the fake clock beyond the timeout, and asserts `consent_timed_out()` is true. Also assert renewal clears the consent timestamp.

- [ ] **Step 2: Run the control test and verify RED**

Run: `python -m unittest tests.test_job_control -v`
Expected: FAIL because consent timeout state/API does not exist.

- [ ] **Step 3: Write failing manager admission test**

Create a manager with `question_max_pending_jobs=1` and a short consent timeout. Submit job A, force it to `AWAITING_CONSENT`, advance the fake clock past timeout, run/allow one monitor iteration, then submit job B. Assert A is terminal/cancelled, A's waiter/task exits, and B is accepted.

- [ ] **Step 4: Run manager test and verify RED**

Run: `python -m unittest tests.test_job_manager -v`
Expected: FAIL because abandoned consent still counts against admission indefinitely.

- [ ] **Step 5: Implement minimal consent timing**

In `JobControl`, set `consent_requested_at` exactly when `_expire()` transitions RUNNING -> AWAITING_CONSENT, preserve it while waiting, clear it on successful renewal and terminal transitions, and expose a lock-protected async timeout check or equivalent manager-safe API. Do not use wall-clock time; use the injected monotonic clock.

- [ ] **Step 6: Implement manager cleanup**

In `_monitor_loop`, when a record is `AWAITING_CONSENT` and timed out, stop/cancel the owned task exactly once so the record becomes terminal and releases admission immediately. Do not touch jobs already DELIVERING/TERMINAL.

- [ ] **Step 7: Add config/docs and validation test**

Add `QUESTION_CONSENT_TIMEOUT_SEC=900` to `.env.example`; document that abandoned Continue/Stop prompts auto-cancel after 15 minutes. Validate positive finite values in Pydantic and add a regression test for invalid NaN/non-positive values.

- [ ] **Step 8: Run targeted tests GREEN**

Run the job-control, job-manager and config regression suites; expected zero failures.

---

### Task 2: Preserve conversation chronology under out-of-order completion

**Files:**
- Modify: `app/core/context.py`
- Modify: `app/core/job_manager.py`
- Modify: `app/bot/question_runner.py`
- Test: create `tests/test_controlled_memory_order.py`

**Interfaces:**
- Extend `JobSubmission` with immutable `conversation_order: int` supplied from Telegram `request_message_id`.
- Extend `ChatMemory.push_exchange(..., order_key: int | None = None)` so controlled exchanges with an order key are returned by `history_for()` in ascending order key, independent of delivery completion order.
- Legacy `push()` and `push_exchange()` calls without `order_key` preserve append-order behavior.

- [ ] **Step 1: Write failing memory-order test**

Push controlled exchange B with order key 102, then exchange A with order key 101, then assert `history_for()` returns A user/assistant before B user/assistant.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_controlled_memory_order -v`
Expected: FAIL because `push_exchange` has no ordering API and current deque commits by completion order.

- [ ] **Step 3: Implement minimal ordered storage**

Keep public history output unchanged (`list[dict[str,str]]`). Store complete exchanges atomically with optional order keys and flatten them when building history. For keyed exchanges sort by key; for unkeyed legacy entries preserve insertion semantics. Do not expose internal metadata in model history.

- [ ] **Step 4: Wire controlled submission/delivery**

Set `conversation_order=message.message_id` in `submit_controlled_question()`. In `QuestionProcessor.deliver()`, pass that key to memory commit. Do not change the history snapshot captured at submission.

- [ ] **Step 5: Add integration regression**

Create two controlled records for one conversation, complete B before A, deliver both, then assert a subsequent memory snapshot is chronological by Telegram request order.

- [ ] **Step 6: Run targeted tests GREEN**

Run controlled-memory test plus existing forum-topic isolation and question-delivery tests.

---

### Task 3: Bound status retry arithmetic and terminal retry lifetime

**Files:**
- Modify: `app/bot/job_status.py`
- Test: `tests/test_job_status.py`

**Interfaces:**
- Add pure helper `_retry_delay(attempt: int) -> float` whose exponent is capped before exponentiation and whose output never exceeds `_STATUS_RETRY_MAX_SEC`.
- Add terminal status retry cap `_TERMINAL_STATUS_MAX_RETRIES` (small finite value, e.g. 5). After the cap, drop presenter bookkeeping for that terminal job instead of rescheduling forever.
- Non-terminal status updates continue retrying because the controls remain operationally relevant.

- [ ] **Step 1: Write failing arithmetic test**

Assert `_retry_delay(1) == 0.5`, a normal sequence reaches the 30-second cap, and `_retry_delay(2000) == 30.0` without OverflowError.

- [ ] **Step 2: Verify RED**

Run: `python -m unittest tests.test_job_status -v`
Expected: FAIL because helper/capped exponent does not exist.

- [ ] **Step 3: Write failing terminal-horizon test**

Create a terminal snapshot whose Telegram edit repeatedly raises a retryable exception. Drive retries beyond the terminal limit and assert no scheduled task/state/lock remains for that job.

- [ ] **Step 4: Verify RED**

Expected: FAIL because terminal retries currently have no finite horizon.

- [ ] **Step 5: Implement minimal bounded retry logic**

Cap exponent before `2 ** exponent`; do not compute an unbounded integer first. When scheduling a retry for a terminal job, stop after `_TERMINAL_STATUS_MAX_RETRIES` and release presenter bookkeeping.

- [ ] **Step 6: Run job-status suite GREEN**

Run all presenter tests and verify existing replacement-message ownership/throttling behavior is unchanged.

---

### Task 4: Restore lifecycle-boundary observability without leaking payloads

**Files:**
- Modify: `app/core/job_manager.py`
- Test: `tests/test_job_manager.py`

**Interfaces:**
- Add module logger.
- Unexpected exceptions at the JobManager terminal boundary log `job_id`, current state/stage, and exception traceback only; do not interpolate submission question/history/quoted/prepared/result content.
- `UserFacingJobError` remains expected and does not need an error traceback.

- [ ] **Step 1: Write failing logging test**

Use `assertLogs('app.core.job_manager', level='ERROR')` around an execute/prepare failure containing a unique exception marker. Assert log output includes the job id and exception type/marker, while unique question/quoted/history secrets do not appear.

- [ ] **Step 2: Verify RED**

Run the manager suite and confirm failure because no terminal-boundary traceback is emitted.

- [ ] **Step 3: Implement minimal logging**

Call `logger.exception("Unexpected controlled job failure job_id=%s state=%s", record.job_id, record.control.state)` inside the generic exception boundary before sanitizing the user-facing error.

- [ ] **Step 4: Run targeted suite GREEN**

Confirm logging test plus terminal pruning tests pass.

---

### Task 5: Make multipart primary-delivery semantics explicit and safe

**Files:**
- Modify: `app/bot/question_runner.py` only if required by the failing test
- Test: `tests/test_question_delivery.py`
- Modify: `README.md`

**Interfaces:**
- Primary multi-part delivery remains non-replayed within a single owned job.
- If part N fails after earlier parts were sent, the job must terminate FAILED and must not commit memory/stats as a complete answer.
- Document that a user manually retrying the whole question may see already-sent leading parts again; exactly-once cross-job delivery is out of scope because Telegram has no transactional multi-message send.

- [ ] **Step 1: Write failure-boundary regression test**

Make part 1 send successfully and part 2 fail. Assert `delivery_committed` remains false, memory is unchanged, success stats are unchanged, and job state becomes FAILED.

- [ ] **Step 2: Verify behavior**

Run the test. If it passes immediately, keep it as a characterization test because no production change is required; do not modify production code merely to satisfy the plan.

- [ ] **Step 3: Add documentation**

Document the at-least-once manual-retry implication and the completion boundary (all primary answer parts + accounting + memory).

- [ ] **Step 4: Run delivery suites GREEN**

Run `test_question_delivery.py` and `test_telegram_delivery.py`.

---

### Task 6: Independent post-fix audit and iterative closure

**Files:**
- Potentially any file implicated by a newly reproduced P1/P2 finding.
- Tests: add one regression test per new reproduced bug before fixing it.

**Interfaces:**
- No new feature contract; this is a verification/audit gate.

- [ ] **Step 1: Run focused renewable-job suites**

Run job control/manager/operations/router/status/telegram/lifecycle/renewal/delivery/memory/thread/dedupe tests.

- [ ] **Step 2: Run full repository verification**

Run `python tests/run_tests.py`, `python -m unittest discover -s tests -p 'test_*.py' -v`, Ruff, `pip check`, `compileall`, `pip-audit`, Compose validation, and Docker build exactly as CI does.

- [ ] **Step 3: Re-audit the final diff**

Review consent generation races, timeout/cancellation ownership, admission accounting, ordering semantics, status retry cleanup, shutdown while delivering, terminal pruning, payload privacy, and legacy-mode compatibility.

- [ ] **Step 4: Loop on every serious finding**

For each reproducible P1/P2 issue: write a failing regression test, verify RED, implement minimal fix, verify GREEN, rerun affected integration suites, then repeat the audit. Do not close the loop based only on code inspection.

- [ ] **Step 5: Final evidence gate**

Only report the PR as having no remaining serious reproduced issue after fresh CI on the final head is green and the final diff audit finds no P1/P2 issue. Record any residual P3/known limitations explicitly instead of hiding them.
