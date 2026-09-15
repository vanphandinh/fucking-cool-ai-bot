# xKiro Provider Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy xKiro as the secondary text/vision fallback while preserving Chainnode as the primary provider and retiring B.AI without creating a period where production lacks a qualified primary route.

**Architecture:** Build and validate the xKiro adapter behind explicit credentials/models first, qualify xKiro with the retained live probe, then remove B.AI and switch configured orders to `chainnode,xkiro`. Chainnode's already-qualified text/vision split remains unchanged throughout rollout, so xKiro is never on the critical path until it passes live qualification.

**Tech Stack:** Python 3.11+, httpx, pydantic-settings, unittest, Docker Compose, existing provider registry/router, GitHub Actions/ruff.

**Spec:** `docs/superpowers/specs/2026-09-16-xkiro-provider-migration-design.md`

## Global Constraints

- Chainnode remains production primary for both text and vision.
- Keep `CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash`.
- Keep `CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor`.
- Final orders are `TEXT_PROVIDER_ORDER=chainnode,xkiro` and `VISION_PROVIDER_ORDER=chainnode,xkiro`.
- xKiro must pass `/v1/models`, plain chat, structured tool call, tool continuation, and vision qualification before production fallback traffic is enabled.
- A candidate advertised as free must have `access_tier=free`, zero input price, and zero output price in the live xKiro catalog response.
- Do not copy `BAI_API_KEY` into `XKIRO_API_KEY`.
- Do not modify provider retry budgets, health semantics, tool budgets, question controls, or the one-image limit.
- B.AI is removed only after xKiro adapter/config/probe tests are green and Chainnode-only operation remains green.

---

### Task 1: Establish a clean implementation branch and baseline

**Files:**
- Read: existing source/tests on `main`
- Use: `docs/superpowers/plans/2026-09-16-xkiro-provider-migration.md`

**Interfaces:**
- Consumes current `main` after PR #52 Chainnode split routing.
- Produces an isolated implementation branch based on current `main`.

- [ ] **Step 1: Create an isolated worktree/branch from current `main`**

Use the `superpowers:using-git-worktrees` workflow at execution time. Suggested branch:

```bash
git fetch origin
git worktree add ../fucking-cool-ai-bot-xkiro -b feat/xkiro-fallback origin/main
cd ../fucking-cool-ai-bot-xkiro
```

- [ ] **Step 2: Run the unchanged baseline suite**

```bash
python tests/run_tests.py
ruff check .
```

Expected: PASS before any migration change. If baseline is not green, stop migration work and fix/reconcile the baseline first.

- [ ] **Step 3: Confirm current Chainnode production defaults**

```bash
python -m unittest tests.test_chainnode_provider tests.test_chainnode_probe tests.test_chainnode_vision_probe -v
```

Expected: PASS, preserving the qualified Chainnode split.

---

### Task 2: Implement xKiro behind inactive configuration

**Files:**
- Create: `app/ai/xkiro.py`
- Create: `tests/test_xkiro_provider.py`
- Modify: `app/config.py`
- Modify: `app/ai/registry.py`

**Interfaces:**
- Produces provider key `xkiro` with text and vision slots.
- xKiro remains inactive when `XKIRO_API_KEY` is empty.

- [ ] **Step 1: Follow Tasks 1-3 of the migration implementation plan using TDD**

Implement:

```text
XKIRO_API_KEY
XKIRO_TEXT_MODEL
XKIRO_VISION_MODEL
XKIRO_REQUEST_TIMEOUT_SEC=60.0
```

Use `https://api.xkiro.com/v1`, OpenAI-compatible Chat Completions, and explicit `stream=false`.

- [ ] **Step 2: Keep Chainnode first in provider order tests**

Expected configured order with both providers enabled:

```text
text   = chainnode -> xkiro
vision = chainnode -> xkiro
```

- [ ] **Step 3: Run focused tests**

```bash
python -m unittest \
  tests.test_xkiro_provider \
  tests.test_chainnode_provider \
  tests.test_provider_registry \
  tests.test_config_regressions -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add app/ai/xkiro.py app/config.py app/ai/registry.py tests/
git commit -m "feat: add xkiro fallback provider"
```

**Gate A:** xKiro code exists and is testable, but production still works with Chainnode alone if `XKIRO_API_KEY` is absent.

---

### Task 3: Build the live xKiro qualification probe

**Files:**
- Create: `scripts/probe_xkiro.py`
- Create: `tests/test_xkiro_probe.py`

**Interfaces:**
- Reads `XKIRO_API_KEY` only from environment.
- Calls `GET https://api.xkiro.com/v1/models` and `POST https://api.xkiro.com/v1/chat/completions`.
- Emits JSONL probe records and non-zero exit status on a hard-gate failure.

- [ ] **Step 1: Implement the offline probe tests before the script**

Hard gates:

```text
model exists
access_tier == free
input price == 0
output price == 0
tools == true
vision == true for vision candidate
plain chat succeeds
structured tool call succeeds
tool continuation succeeds
vision request succeeds for vision candidate
```

- [ ] **Step 2: Implement the probe and run offline tests**

```bash
python -m unittest tests.test_xkiro_probe -v
```

Expected: PASS with mocked HTTP only.

- [ ] **Step 3: Commit**

```bash
git add scripts/probe_xkiro.py tests/test_xkiro_probe.py
git commit -m "test: add xkiro live qualification probe"
```

**Gate B:** no production routing change yet; xKiro can now be qualified independently from runtime.

---

### Task 4: Qualify xKiro models from the live catalog

**Files:**
- No source change required for each model candidate.
- Record accepted model IDs in deployment configuration and `docs/XKIRO.md` only after qualification.

**Interfaces:**
- Produces one accepted text model ID and one accepted vision model ID.

- [ ] **Step 1: Export the API key without putting it in shell history or Git**

Use the deployment secret manager/environment mechanism already used for provider credentials. Confirm:

```bash
test -n "$XKIRO_API_KEY"
```

- [ ] **Step 2: Discover candidates from live `/v1/models`**

Run the probe in catalog/list mode and select only records satisfying the free hard gates. Prefer current DeepSeek V4.1 Flash for text when its live record passes the gates; otherwise select the highest-priority current free text model that passes tools and compatibility probes. For vision, select only a live free model with both vision and tools.

- [ ] **Step 3: Probe text candidates**

For each candidate, require all of:

```text
HTTP success
non-empty plain response
structured tool call with valid id/name/JSON arguments
successful tool continuation
no internal tool markup leak
acceptable non-streaming latency under the existing attempt budget
```

- [ ] **Step 4: Probe vision candidates with the existing deterministic image fixture**

Require correct image-content recognition plus structured vision tool call/continuation where supported by the probe contract.

- [ ] **Step 5: Choose production fallback models deterministically**

Ranking order:

```text
1. passes every hard gate
2. lowest compatibility error rate across repeated probe runs
3. lower p95 latency
4. larger context window as tie-breaker
```

Do not rank a paid candidate above a free candidate for this fallback role.

**Gate C:** `XKIRO_TEXT_MODEL` and `XKIRO_VISION_MODEL` are backed by live probe evidence, not catalog marketing copy.

---

### Task 5: Migrate environment configuration safely

**Files:**
- Modify: `.env.example`
- Modify: `scripts/sync_env.py`
- Modify: `tests/test_sync_env.py`
- Modify: `tests/test_provider_env_migration.py`

**Interfaces:**
- Final canonical orders: `chainnode,xkiro`.
- Removed keys: every `BAI_*` key.

- [ ] **Step 1: Add failing environment migration tests**

Verify these exact transformations:

```text
TEXT_PROVIDER_ORDER=bai            -> chainnode,xkiro
TEXT_PROVIDER_ORDER=chainnode,bai  -> chainnode,xkiro
TEXT_PROVIDER_ORDER=bai,chainnode  -> chainnode,xkiro
VISION_PROVIDER_ORDER=bai          -> chainnode,xkiro
```

Also verify a custom order containing neither `bai` nor removed providers is preserved.

- [ ] **Step 2: Update `.env.example`**

Canonical provider section:

```env
CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0

XKIRO_API_KEY=
XKIRO_TEXT_MODEL=
XKIRO_VISION_MODEL=
XKIRO_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

- [ ] **Step 3: Update `sync_env.py`**

Requirements:

```text
remove BAI_* because they no longer exist in .env.example
preserve Chainnode credentials/models
preserve explicitly supplied XKIRO_* values
rewrite orders containing bai so an unregistered provider is never left behind
never copy BAI_API_KEY to XKIRO_API_KEY
remain idempotent on second run
```

- [ ] **Step 4: Run migration tests**

```bash
python -m unittest tests.test_sync_env tests.test_provider_env_migration -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add .env.example scripts/sync_env.py tests/test_sync_env.py tests/test_provider_env_migration.py
git commit -m "chore: migrate provider config from bai to xkiro fallback"
```

**Gate D:** upgrading an existing deployment cannot leave `bai` in active provider order.

---

### Task 6: Remove B.AI implementation and B.AI-specific behavior

**Files:**
- Delete: `app/ai/bai.py`
- Delete: `scripts/probe_bai.py`
- Delete: `tests/test_bai_provider.py`
- Delete: `tests/test_bai_probe.py`
- Delete: `docs/BAI_INTEGRATION.md`
- Modify: `app/ai/base.py`
- Modify: `tests/test_fresh_synthesis_routing.py`
- Modify: provider-related tests containing active B.AI assumptions

**Interfaces:**
- Active registry contains only `chainnode` and `xkiro`.

- [ ] **Step 1: Search before deletion**

```bash
git grep -n -E 'app\.ai\.bai|build_bai|make_bai|BAI_|api\.b\.ai|\bbai\b' -- ':!docs/superpowers/**'
```

Record every active match and assign it to code, test, or maintained documentation cleanup.

- [ ] **Step 2: Remove B.AI files and imports**

```bash
git rm app/ai/bai.py scripts/probe_bai.py tests/test_bai_provider.py tests/test_bai_probe.py docs/BAI_INTEGRATION.md
```

- [ ] **Step 3: Remove the no-tools workaround only if the search proves B.AI was its sole consumer**

Fresh synthesis must still send no `tools` and must never execute post-budget tool calls. It no longer needs to assert a B.AI-specific `tool_choice=none` field.

- [ ] **Step 4: Run provider/fresh-synthesis regressions**

```bash
python -m unittest \
  tests.test_provider_registry \
  tests.test_provider_routing \
  tests.test_provider_transport_retry \
  tests.test_provider_retry_health \
  tests.test_provider_portability \
  tests.test_fresh_synthesis_routing \
  tests.test_tool_fallback_recovery -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: retire bai provider"
```

**Gate E:** B.AI is no longer executable or configurable, while Chainnode/xKiro routing remains green.

---

### Task 7: Update maintained documentation and operator runbook

**Files:**
- Create: `docs/XKIRO.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/CHAINNODE.md`
- Modify: `docs/ENV_SYNC.md`

**Interfaces:**
- Documentation reflects Chainnode primary + xKiro fallback only.

- [ ] **Step 1: Document the final architecture**

```text
Telegram
  -> router
       -> Chainnode text/vision primary
       -> xKiro text/vision fallback
```

- [ ] **Step 2: Document xKiro qualification and quota behavior**

State that model availability/free status is external and must be checked with the retained `/v1/models` probe before changing production model IDs.

- [ ] **Step 3: Document rollback**

Immediate rollback after migration is Chainnode-only, not B.AI:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

Keep `XKIRO_*` credentials/models present but unused if operator wants a fast re-enable.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/README.md docs/CHAINNODE.md docs/ENV_SYNC.md docs/XKIRO.md
git commit -m "docs: document chainnode primary and xkiro fallback"
```

---

### Task 8: Full audit before opening the PR

**Files:**
- Entire repository except historical `docs/superpowers/**` records.

**Interfaces:**
- Produces merge-ready implementation with no active B.AI references.

- [ ] **Step 1: Run dead-reference scans**

```bash
! git grep -n -E 'app\.ai\.bai|build_bai|make_bai|BAI_|api\.b\.ai' -- ':!docs/superpowers/**'
! git grep -n -E 'TEXT_PROVIDER_ORDER=.*bai|VISION_PROVIDER_ORDER=.*bai' -- ':!docs/superpowers/**'
```

Expected: both commands succeed because grep finds no active matches.

- [ ] **Step 2: Run the complete test suite**

```bash
python tests/run_tests.py
```

Expected: PASS.

- [ ] **Step 3: Run lint**

```bash
ruff check .
```

Expected: PASS.

- [ ] **Step 4: Review the complete diff against `origin/main`**

```bash
git diff --check origin/main...HEAD
git diff --stat origin/main...HEAD
git diff origin/main...HEAD
```

Verify no secret, xKiro API response containing private account data, or accidental unrelated change is present.

**Gate F:** full suite + lint + dead-reference scan + diff review all green.

---

### Task 9: Open PR and run CI review loop

**Files:**
- GitHub PR only.

**Interfaces:**
- Produces a reviewable migration PR into `main`.

- [ ] **Step 1: Push implementation branch**

```bash
git push -u origin feat/xkiro-fallback
```

- [ ] **Step 2: Open PR**

Use title:

```text
feat: add xKiro fallback and retire B.AI
```

PR body must state:

```text
Chainnode remains primary for text and vision.
xKiro is secondary fallback only.
BAI runtime/config/tests/docs are removed.
xKiro candidate models were selected through live /v1/models + compatibility probes.
Rollback is Chainnode-only provider order.
```

- [ ] **Step 3: Wait for actual CI results only through GitHub Actions and inspect failures**

Do not merge on local tests alone. Fix any deterministic CI failure on the same branch and repeat full verification.

**Gate G:** required CI checks green and review concerns resolved.

---

### Task 10: Production deployment with Chainnode-first canary

**Files:**
- Deployment `.env` / secret manager; no new source commit unless a real defect is found.

**Interfaces:**
- Final production routing uses Chainnode first and xKiro second.

- [ ] **Step 1: Before deploy, add xKiro credentials/models to production secrets**

Required values:

```text
XKIRO_API_KEY=<deployment secret>
XKIRO_TEXT_MODEL=<accepted text model from Gate C>
XKIRO_VISION_MODEL=<accepted vision model from Gate C>
```

Never commit these values.

- [ ] **Step 2: Deploy code while initially forcing Chainnode-only routing**

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

Run:

```bash
python scripts/sync_env.py
docker compose up -d --build
docker compose logs --tail=200 bot
```

Smoke test text, vision, web-tool, URL-tool, and fresh-synthesis paths. Expected provider: Chainnode.

- [ ] **Step 3: Enable xKiro as fallback**

```env
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Restart/reload the bot using the existing deployment procedure.

- [ ] **Step 4: Prove Chainnode remains primary**

Send normal text and vision requests while Chainnode is healthy. Provider metrics/log-safe observability must show Chainnode serving the requests, with no unnecessary xKiro traffic.

- [ ] **Step 5: Prove fallback deliberately**

In a controlled maintenance test, make the Chainnode route unavailable using a reversible configuration/test mechanism, then send one text and one vision request. Verify:

```text
fallback provider = xkiro
completed tool results are not executed twice
provider transition metrics show chainnode -> xkiro
```

Immediately restore Chainnode and verify subsequent normal requests return to Chainnode primary.

**Gate H:** both normal primary routing and controlled fallback routing work in production.

---

### Task 11: Post-deploy rollback and acceptance criteria

**Files:**
- Deployment configuration only.

**Interfaces:**
- Provides a one-step provider rollback without restoring B.AI code.

- [ ] **Step 1: Use Chainnode-only rollback if xKiro shows unexpected production behavior**

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

Restart/reload using the existing deployment procedure. No code rollback is required for an xKiro-only incident.

- [ ] **Step 2: Accept the migration only when all conditions are true**

```text
Chainnode remains primary for healthy text requests.
Chainnode remains primary for healthy vision requests.
xKiro serves text when Chainnode is deliberately unavailable.
xKiro serves vision when Chainnode is deliberately unavailable.
No tool is executed twice across fallback.
No BAI credential/config/runtime reference remains active.
Full tests and ruff are green.
Production error/fallback metrics contain no new severe regression.
```

- [ ] **Step 3: Keep the xKiro live probe as the required pre-change gate for future model rotation**

A future xKiro model change modifies deployment model IDs only after the candidate passes the same live qualification. It must not require adding a new hard-coded allowlist to runtime.
