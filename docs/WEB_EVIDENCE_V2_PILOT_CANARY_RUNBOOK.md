# Web Evidence V2 — PILOT CANARY RUNBOOK (allowlist rollout)

Operational plan for the first pilot. **Nothing in this document has been executed
on production**: every flag ships OFF and no chat is allowlisted yet. Approval for
each step stays with the project owner.

Related tracked documents: the execution plan and Gate G1 evidence in
[WEB_EVIDENCE_V2_PILOT_EXECUTION_PLAN_20261016.md](WEB_EVIDENCE_V2_PILOT_EXECUTION_PLAN_20261016.md),
the 21-question guide in [WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md](WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md),
and the environment contract in [.env.example](../.env.example).

## 1. Scope: two switches, one rollback

| Setting | Value | Effect |
| --- | --- | --- |
| `WEB_EVIDENCE_V2_ENABLED` | `false` (global switch, stays off) | nobody runs V2 |
| `WEB_EVIDENCE_V2_GROUP_IDS` | e.g. `-1001234567890,-1009876543210` | only these chat ids run V2 |
| `WEB_AUTO_FETCH_ENABLED` | `false` | stays off until the pilot passes |

A chat outside the allowlist — and any request without a chat id — keeps the
identical settings object, so V1 behaviour is byte-for-byte. `WEB_AUTO_FETCH_ENABLED`
requires `WEB_EVIDENCE_V2_ENABLED=true`, and the canary only promotes the flag, so
auto-fetch cannot switch on from the allowlist alone.

Boot states the scope once, so a canary deployment cannot be mistaken for a global
rollout:

```
web_evidence_v2 scope=off policy=v1                       # no ids, flag off (default)
web_evidence_v2 scope=canary groups=2 policy=v2-for-allowlist
web_evidence_v2 scope=global policy=v2                    # global flag on
```

## 2. Preconditions before allowlisting anyone

1. Gate G2 from the execution plan is met: full suite + ruff + Markdown links +
   canonical-env scans green, and the 21-question set run twice in both modes with
   V2 at least equal to V1 and zero guard errors.
2. Two consecutive clean runs of the live-smoke artifact under
   `scripts/web_evidence_live_smoke.py`, audited with
   `scripts/web_evidence_pilot_audit.py` (exit 0) *and* manually graded with the
   rubric in the plan.
3. Owner approval to flip `WEB_EVIDENCE_V2_GROUP_IDS` on the shared deployment.

## 3. Turning the canary on

```
# .env (or the deployment's environment), then restart the bot
WEB_EVIDENCE_V2_ENABLED=false
WEB_EVIDENCE_V2_GROUP_IDS=<chat id>[,<chat id>...]
WEB_AUTO_FETCH_ENABLED=false
```

Verify after restart: the boot line reads `scope=canary`, and a question in an
allowlisted chat logs one `v2_canary chat_id=...` line. Add ids one at a time.

## 4. Monitoring signals

Per-answer log lines (INFO, `app.core.orchestrator`):

| Line | Fields | Watch for |
| --- | --- | --- |
| `v2_canary` | `chat_id`, `scope=allowlist`, `policy=v2` | unexpected chats — scope drift |
| `v2_citation_guard` | `detected`, `allowed`, `removed`, `reasons`, `error` | `error` must stay 0; rising `removed` share means the model invents links |
| `ai_usage` | `provider`, `prompt`, `completion`, `total` | cost per question; `-` means the provider reported nothing |

Per-artifact audit (offline, read-only):

```
python scripts/web_evidence_live_smoke.py --mode both --no-auto-fetch  # real provider
python scripts/web_evidence_pilot_audit.py <artifact.json>             # exit 1 = stop condition
python tests/run_tests.py
```

The audit prints, per mode: rows/completed/failed/timeout, latency p50/p95, median
prompt and completion tokens with the number of rows that reported usage, guard
`detected`/`removed`/removed-rate, guard errors, citations outside the allowed set,
and the share of answers that reached the user carrying the unverified-citation
marker. V1 rows never run the guard, so their guard columns stay zero by design.

## 5. Stop conditions (kill switch)

| Condition | Action |
| --- | --- |
| Guard error ≥ 1 (an answered question lost) | V2 OFF immediately |
| Any citation outside the guard's allowed set on a real model | V2 OFF immediately |
| Same stale-config bad answer twice in a day | pause the pilot, return to retrieval |
| Latency p95 above the V1 baseline by more than the pilot budget | pause, investigate before adding chats |
| Repeated provider `usage` absent + rising cost suspicion | treat as unmeasured, do not claim a cost figure |

Kill switch = remove the ids (or set `WEB_EVIDENCE_V2_ENABLED=false`) and restart:

```
WEB_EVIDENCE_V2_GROUP_IDS=
WEB_EVIDENCE_V2_ENABLED=false
WEB_AUTO_FETCH_ENABLED=false
```

Rollback is a configuration change only: no schema, cache namespace or data
migration, and the flag-off path is the unchanged V1 code path. Re-verify after
rollback with one question in the previously allowlisted chat and confirm no
`v2_canary` line is logged.

## 6. Exit criteria

48 hours of canary with no stop condition fired, plus a before/after table (latency,
real token usage, guard removal rate, marker rate, 21-question quality) added to the
pilot evidence. Only then consider the global flag or auto-fetch, each as its own
approved step.

## 7. Not verified by this runbook

Live provider behaviour, real Telegram rendering, and production latency/token
figures are **NOT VERIFIED** here: the tracked artifacts cover deterministic and
fixture-level evidence plus whatever the operator records while executing §3–§5.
Do not read this document as measured production data.
