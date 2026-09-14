# Renewable Telegram question controls

This guide documents the Release 1 controlled-question mode implemented behind `QUESTION_CONTROLS_ENABLED`.

## Purpose

Long research questions no longer need one hard wall-clock deadline when controlled mode is enabled. A question becomes an in-memory job with bounded leaf operations, real progress events, renewable consent windows and an explicit Stop action.

The feature is intentionally **off by default**. `QUESTION_CONTROLS_ENABLED=0` keeps the legacy handler and `QUESTION_TIMEOUT_SEC` outer timeout unchanged for rollback.

## Runtime modes

### Legacy mode

```env
QUESTION_CONTROLS_ENABLED=0
```

The existing Telegram handler remains active. `QUESTION_TIMEOUT_SEC` still bounds the whole end-user question, including chat locking, model/tool work and delivery.

### Renewable mode

```env
QUESTION_CONTROLS_ENABLED=1
QUESTION_RENEWAL_INTERVAL_SEC=180
QUESTION_PROGRESS_INTERVAL_SEC=25
QUESTION_MAX_INFLIGHT_OPERATIONS=2
QUESTION_MAX_PENDING_JOBS=20
QUESTION_MAX_JOBS_PER_USER=2
AI_ATTEMPT_TOTAL_TIMEOUT_SEC=90
URL_READ_TOTAL_TIMEOUT_SEC=40
TOOL_CALL_TOTAL_TIMEOUT_SEC=45
```

There is no hard deadline around the whole controlled question. The renewal interval is an authorization window, not a kill timer.

## Time semantics

- The first processing grant starts when a leaf operation actually becomes active, not while the job is waiting for capacity.
- When the renewal interval is exhausted, the job enters `AWAITING_CONSENT`.
- An operation that already started may finish under its own local timeout.
- If the renewal boundary is crossed while a leaf operation is being released, that release emits the renewal transition immediately; it does not depend on a later monitor tick.
- While consent is pending, the job does not dispatch a new provider attempt, tool attempt, search fallback or URL-reader fallback.
- Pressing **Tiếp tục** grants one new interval for the same job and invalidates the old continue generation. Double-clicking cannot grant two intervals.
- A consent prompt that is left unanswered for 15 minutes auto-cancels the job so abandoned prompts cannot occupy admission capacity forever. The timeout is bound to the consent generation being waited on, so a stale timeout cannot cancel a newer successful renewal.
- Pressing **Dừng** cancels owned asyncio work and waits for local cleanup. Client-side cancellation does not claim that an upstream server or already-running synchronous thread was killed.
- Total elapsed time shown to the user does not reset after renewal.
- Queue time and consent time are excluded from operation budgets.

## Local bounds and capacity

Every controlled leaf operation is gated by the shared operation semaphore. Provider attempts, URL/search backends and media loading keep local time bounds even though the complete question has no global deadline.

`TOOL_CALL_TOTAL_TIMEOUT_SEC` is an aggregate active-leaf budget for one logical model tool call. Nested search/URL leaves inherit that budget and use the smallest remaining active budget. Time spent waiting for the shared operation semaphore or for **Tiếp tục** consent is not charged to this aggregate tool budget.

DDGS and other explicitly bounded synchronous work use a dedicated thread limiter. Cancelling the asyncio waiter does not release thread capacity early; the slot is returned only after the real synchronous call exits. This prevents timed-out thread work from accumulating without bound.

Search auto mode keeps shared cache behavior but does not share one live singleflight execution across renewable jobs, because two jobs can have different owners and consent states.

## State preservation

Renewal continues the same coroutine and request state. It does not restart the orchestrator. In particular, renewal does not reset:

- provider retry counters or provider health;
- tool-call counters/rounds;
- portable messages and successful tool outputs;
- per-question URL cache;
- already-completed evidence;
- the immutable history snapshot captured at admission.

Tool results are retained by original tool-call index as they finish. Canonical tool messages are emitted in model order once the batch is ready. A completed tool is not replayed merely because the job was renewed or provider routing changed.

If a provider returns a final answer after the renewal boundary was crossed, the answer may move directly to delivery without asking the user to renew just to receive already-computed output. A tool-call response, by contrast, must wait for consent before dispatching its next tool operation.

## Telegram permissions and callbacks

Callback payloads use:

```text
qj:<job_id>:<generation>:c
qj:<job_id>:<generation>:s
```

The callback handler acknowledges Telegram first, then validates the job, actor, chat and status-message ID.

- Only the question owner can continue a job.
- The owner can stop the job.
- IDs in `ADMIN_IDS` can stop a job but cannot continue it.
- Continue requires the current generation.
- Stop remains valid from an older button for the same current status message so a status refresh cannot accidentally remove the user's ability to stop.
- Missing/restarted jobs return an expired/stale result and are never recreated from a callback.

Status updates are coalesced. Normal progress is limited by `QUESTION_PROGRESS_INTERVAL_SEC`; consent and terminal transitions are prioritized and wake a throttled presenter promptly. A forced transition marks another refresh as pending instead of cancelling Telegram I/O already in progress. The UI does not invent percentages or claim the model is thinking when a network/tool operation is the actual active stage.

If the status message was deleted, the presenter creates at most one successfully-sent managed replacement and atomically updates the job's status-message ID. Transient edit, replacement-send and replacement-markup failures are retried by the presenter with bounded exponential backoff; Telegram `RetryAfter` uses Telegram's supplied delay. Once a replacement becomes the owned status message, later retries edit that message rather than creating another replacement. If Telegram remains unreachable, retries stay bounded and the job does not receive an automatic renewal. Backoff exponentiation is capped before arithmetic, and a terminal status is attempted only a finite number of times before its presenter bookkeeping is released.

Terminal presenter bookkeeping is released after the terminal refresh settles, and presenter shutdown clears remaining per-job state and scheduled-task references.

## Conversation isolation

Controlled jobs do not hold the legacy per-chat lock for the whole question. Multiple jobs in the same chat/topic may progress independently subject to admission and leaf-operation limits.

Each accepted job gets a copy of conversation history at admission. Results from another job that finishes later are not retroactively inserted into that running snapshot.

Delivered controlled exchanges are retained in Telegram request-message order, not completion order. This keeps later history chronological even when a later question finishes before an earlier long-running question; retention pruning also uses that canonical order so out-of-order completion cannot evict a newer conversational turn by mistake.

## Delivery completion boundary

The controlled-question completion boundary is the successful primary answer commit:

1. all primary answer parts have been sent;
2. provider/fallback/search success accounting has been recorded;
3. `ChatMemory.push_exchange()` has committed the user/assistant exchange; and
4. the job records `delivery_committed=True` immediately, with no cancellable await between the memory commit and that flag.

Before this boundary, an interrupted delivery is failed/interrupted and must not claim successful memory or success accounting. After this boundary, the job is logically `COMPLETED` even if shutdown cancels later enrichment work.

If a multi-part primary answer fails after one or more leading parts were already sent, the job remains uncommitted and ends failed. A manual whole-question retry can therefore repeat those already-sent leading parts; Telegram does not provide a transactional multi-message send, so cross-job exactly-once delivery is not promised in Release 1.

Images and source/footer messages sent after the primary commit are **best-effort extras**. Failure or cancellation of those extras must not replay the primary answer, duplicate memory/stat accounting, or turn an already committed answer into `FAILED`.

## In-memory limitation

Release 1 keeps the registry and checkpoints in RAM. A process restart does not resume a running or paused question. Old buttons cannot resurrect a job after restart. Persistent checkpoints are a separate future release.

Terminal records are retained only as a bounded lookup/outcome cache. Once a job reaches a terminal state, large request/runtime payloads such as question text, quoted text, history, prepare closures, prepared request objects, operation runners, result payloads and event history are released. Identity and authorization metadata needed for snapshots and stale callback validation remains available until the bounded terminal record itself is evicted.

## Shutdown

Shutdown stops admissions and owned controlled jobs before Telegram/search/provider clients are closed. Job tasks and the renewal monitor are cancelled/gathered so controlled work is not intentionally left running against already-closed clients.

Shutdown semantics are state-aware:

- `QUEUED`: execution must not start after cancellation;
- `RUNNING`: active operation/semaphore capacity is released by cleanup;
- `AWAITING_CONSENT`: the consent waiter is woken/cancelled without renewing or dispatching new work;
- `DELIVERING` before the primary commit: interruption is failed/interrupted;
- `DELIVERING` after the primary commit: the job remains `COMPLETED`, while images/sources may be abandoned as best effort.

## Rollout

1. Deploy code with `QUESTION_CONTROLS_ENABLED=0` first.
2. Run the offline/CI gates and inspect startup logs for the selected question mode and configured caps.
3. In a designated test bot/group, smoke text, search, URL, image/topic, multiple renewals, Stop and restart behavior.
4. Only then set `QUESTION_CONTROLS_ENABLED=1` for the intended deployment and recreate/restart the container so env is reloaded.
5. Observe completion/failure/cancellation, renewal requests/acceptance, pending/active jobs and backend timeout sources.

This repository change does not perform deployment or send test messages to a production group.

## Rollback

Set:

```env
QUESTION_CONTROLS_ENABLED=0
```

and recreate/restart the deployment. New questions return to the legacy outer `QUESTION_TIMEOUT_SEC` path. Controlled jobs are RAM-only and therefore are interrupted by process restart; the system does not promise automatic resumption.

## Verification

The CI workflow covers Python 3.11 and 3.12, lint, dependency compatibility, compile checks, offline tests, Docker Compose validation and production image build. Renewable-specific tests additionally cover state renewal, duplicate generation rejection, consent timeout/admission release, stale-timeout generation races, search/provider/tool gating, aggregate tool budgets, thread lifetime capacity, admission/shutdown, callbacks/status rendering, bounded presenter recovery/backoff/cleanup, ordered conversation retention, terminal payload pruning, delivery commit semantics and continuity across multiple renewals.
