# xKiro Provider Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Chainnode as the primary text/vision provider, add xKiro as the secondary fallback, and remove B.AI completely from active runtime code, configuration, probes, tests, and maintained documentation.

**Architecture:** Reuse the existing `OpenAICompatProvider` and capability-aware router. Add a thin `xkiro` provider family with separate text/vision slots, keep the qualified Chainnode split unchanged, and change default provider order to `chainnode,xkiro`. xKiro model IDs are explicit deployment settings and are qualified by a retained live probe against `/v1/models`; runtime never performs catalog discovery.

**Tech Stack:** Python 3.11+, httpx, pydantic-settings, unittest, existing OpenAI-compatible provider/router framework, GitHub Actions/ruff.

**Spec:** `docs/superpowers/specs/2026-09-16-xkiro-provider-migration-design.md`

## Global Constraints

- Chainnode remains primary for text and vision.
- Qualified Chainnode production models stay `cl/cline-free/deepseek-v4.1-flash` for text and `cl/cline-free/muse-spark-1.3-contributor` for vision.
- Default orders become `TEXT_PROVIDER_ORDER=chainnode,xkiro` and `VISION_PROVIDER_ORDER=chainnode,xkiro`.
- xKiro base URL is `https://api.xkiro.com/v1` and runtime uses OpenAI-compatible `POST chat/completions` with `stream=false`.
- Initial xKiro vision slot advertises `max_images=1`; do not increase image limits in this migration.
- xKiro model IDs are configuration, not a runtime hard-coded free-model allowlist.
- Live probe must use `/v1/models` as the model/pricing/capability source of truth.
- Do not migrate or reuse a B.AI API key as an xKiro credential.
- Do not change router retry budgets, provider-health semantics, tool budgets, or question-control behavior.
- Active B.AI runtime code/config/tests/docs must be removed; historical records under `docs/superpowers/` may retain B.AI references.

---

## File Structure

**Create**
- `app/ai/xkiro.py` — xKiro provider-family factory and text/vision slot construction.
- `scripts/probe_xkiro.py` — retained live compatibility/free-tier qualification probe.
- `tests/test_xkiro_provider.py` — adapter/config/wire-contract tests.
- `tests/test_xkiro_probe.py` — offline tests for probe parsing and hard gates.
- `docs/XKIRO.md` — maintained operator/provider guide.

**Modify**
- `app/config.py` — xKiro settings, Chainnode-first defaults, validation.
- `app/ai/registry.py` — register `chainnode` and `xkiro`, remove `bai`.
- `app/ai/base.py` — remove B.AI-only no-tools workaround if no remaining consumer exists.
- `.env.example` — Chainnode-first defaults and xKiro settings; remove `BAI_*`.
- `scripts/sync_env.py` — migrate provider order away from `bai` while dropping removed B.AI keys.
- `tests/test_chainnode_provider.py` — assert Chainnode remains primary and xKiro is fallback.
- `tests/test_provider_registry.py` — replace B.AI family/default assertions with xKiro + Chainnode-first assertions.
- `tests/test_provider_env_migration.py` — verify B.AI removal and provider-order migration.
- `tests/test_sync_env.py` — cover order canonicalization and removed B.AI keys.
- `tests/test_free_routing.py` — repurpose to current Chainnode-primary/xKiro-fallback defaults.
- `tests/test_fresh_synthesis_routing.py` — remove B.AI-specific `tool_choice=none` expectation and use generic/xKiro provider fixtures.
- `README.md` — update architecture, defaults, setup, fallback examples.
- `docs/README.md` — replace B.AI guide link with xKiro guide.
- `docs/CHAINNODE.md` — state Chainnode is primary and xKiro is fallback.
- `docs/ENV_SYNC.md` — document B.AI key removal and provider-order migration.
- Any active docs/tests found by repository-wide `bai`/`BAI_` scan.

**Delete**
- `app/ai/bai.py`
- `scripts/probe_bai.py`
- `tests/test_bai_provider.py`
- `tests/test_bai_probe.py`
- `docs/BAI_INTEGRATION.md`

---

### Task 1: Add xKiro Provider Adapter with TDD

**Files:**
- Create: `tests/test_xkiro_provider.py`
- Create: `app/ai/xkiro.py`

**Interfaces:**
- Consumes: `Settings`, `OpenAICompatProvider`, `ProviderCapabilities`, `AIProvider`.
- Produces: `make_xkiro_provider(settings, *, name="xkiro", vision=False) -> OpenAICompatProvider` and `build_xkiro_provider_slots(settings) -> list[AIProvider]`.

- [ ] **Step 1: Write failing adapter tests**

Cover all of these exact behaviors:

```python
XKIRO_BASE_URL = "https://api.xkiro.com/v1"

# text slot
provider.name == "xkiro"
provider.model == settings.xkiro_text_model
str(provider._client.base_url) == "https://api.xkiro.com/v1/"
provider._client.timeout.read == settings.xkiro_request_timeout_sec
provider.capabilities.route == "text"
provider.capabilities.supports_vision is False
provider.capabilities.max_images == 0
provider.explicit_stream is False

# vision slot
provider.model == settings.xkiro_vision_model
provider.capabilities.route == "vision"
provider.capabilities.supports_vision is True
provider.capabilities.max_images == 1

# missing configured model
make_xkiro_provider(... text model empty ...) raises ValueError("XKIRO_TEXT_MODEL")
make_xkiro_provider(... vision model empty ...) raises ValueError("XKIRO_VISION_MODEL")
```

Also assert the wire payload for text with tools contains exactly `model`, `messages`, `tools`, and `stream`, with `stream is False`; no `tool_choice`, `reasoning_effort`, `thinking`, or `enable_thinking` is injected.

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
python -m unittest tests.test_xkiro_provider -v
```

Expected: import/module failures because `app.ai.xkiro` does not exist.

- [ ] **Step 3: Implement the minimal xKiro adapter**

Create `app/ai/xkiro.py` with this shape:

```python
"""xKiro OpenAI-compatible provider integration."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities
from .provider import AIProvider

_BASE_URL = "https://api.xkiro.com/v1"


def make_xkiro_provider(
    settings: Settings,
    *,
    name: str = "xkiro",
    vision: bool = False,
) -> OpenAICompatProvider:
    model = str(
        settings.xkiro_vision_model if vision else settings.xkiro_text_model
    ).strip()
    setting_name = "XKIRO_VISION_MODEL" if vision else "XKIRO_TEXT_MODEL"
    if not model:
        raise ValueError(f"{setting_name} là bắt buộc khi khởi tạo xKiro provider")

    return OpenAICompatProvider(
        name=name,
        base_url=_BASE_URL,
        api_key=settings.xkiro_api_key,
        model=model,
        timeout=settings.xkiro_request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=1 if vision else 0,
        ),
        explicit_stream=False,
    )


def build_xkiro_provider_slots(settings: Settings) -> list[AIProvider]:
    if not settings.xkiro_api_key:
        return []

    slots: list[AIProvider] = []
    if "xkiro" in settings.text_provider_order_list and settings.xkiro_text_model:
        slots.append(make_xkiro_provider(settings))
    if (
        settings.vision_enabled
        and "xkiro" in settings.vision_provider_order_list
        and settings.xkiro_vision_model
    ):
        slots.append(make_xkiro_provider(settings, vision=True))
    return slots
```

- [ ] **Step 4: Run adapter tests**

```bash
python -m unittest tests.test_xkiro_provider -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ai/xkiro.py tests/test_xkiro_provider.py
git commit -m "feat: add xkiro provider adapter"
```

---

### Task 2: Add xKiro Settings and Make Chainnode the Default Primary

**Files:**
- Modify: `app/config.py`
- Modify: `tests/test_config_regressions.py`
- Modify: `tests/test_chainnode_provider.py`
- Modify: `tests/test_free_routing.py`

**Interfaces:**
- Produces settings: `xkiro_api_key: str`, `xkiro_text_model: str`, `xkiro_vision_model: str`, `xkiro_request_timeout_sec: float`.
- Produces default orders: `chainnode,xkiro` for both text and vision.

- [ ] **Step 1: Write failing configuration tests**

Add assertions that:

```python
settings = Settings(_env_file=None)
settings.text_provider_order_list == ["chainnode", "xkiro"]
settings.vision_provider_order_list == ["chainnode", "xkiro"]
settings.xkiro_api_key == ""
settings.xkiro_text_model == ""
settings.xkiro_vision_model == ""
settings.xkiro_request_timeout_sec == 60.0
```

Add validation tests:

```python
Settings(
    _env_file=None,
    xkiro_api_key="x",
    text_provider_order="chainnode,xkiro",
    xkiro_text_model="",
)
# raises ValueError mentioning XKIRO_TEXT_MODEL

Settings(
    _env_file=None,
    xkiro_api_key="x",
    vision_enabled=True,
    vision_provider_order="chainnode,xkiro",
    xkiro_vision_model="",
)
# raises ValueError mentioning XKIRO_VISION_MODEL
```

Keep the existing Chainnode validation symmetrical.

- [ ] **Step 2: Run focused config tests and verify failure**

```bash
python -m unittest tests.test_config_regressions tests.test_chainnode_provider tests.test_free_routing -v
```

Expected: FAIL on missing xKiro settings and old B.AI defaults.

- [ ] **Step 3: Modify `Settings`**

Replace B.AI fields with:

```python
xkiro_api_key: str = ""
xkiro_text_model: str = ""
xkiro_vision_model: str = ""
xkiro_request_timeout_sec: float = Field(default=60.0, gt=0, allow_inf_nan=False)
text_provider_order: str = "chainnode,xkiro"
vision_provider_order: str = "chainnode,xkiro"
```

Remove `bai_api_key`, `bai_text_model`, `bai_vision_model`, and `bai_request_timeout_sec`.

Add model validators for xKiro matching the existing Chainnode rule: only require a model when the API key is non-empty and the provider is selected for that route.

- [ ] **Step 4: Update deployment-default tests**

Change old B.AI-only assertions to verify:

```text
primary text route   = chainnode
fallback text route  = xkiro
primary vision route = chainnode
fallback vision      = xkiro
```

When no credentials are configured, router may have zero active slots even though the configured order contains both provider names. When only Chainnode credentials/models exist, only Chainnode slots are active. When both providers are configured, active names are `("chainnode", "xkiro")`.

- [ ] **Step 5: Run focused tests**

```bash
python -m unittest tests.test_config_regressions tests.test_chainnode_provider tests.test_free_routing -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/config.py tests/test_config_regressions.py tests/test_chainnode_provider.py tests/test_free_routing.py
git commit -m "feat: make chainnode primary with xkiro fallback config"
```

---

### Task 3: Register xKiro and Remove B.AI from Runtime Registry

**Files:**
- Modify: `app/ai/registry.py`
- Modify: `tests/test_provider_registry.py`

**Interfaces:**
- Registry keys after task: exactly `chainnode` and `xkiro`.

- [ ] **Step 1: Write failing registry tests**

Assert:

```python
set(PROVIDER_FACTORIES) == {"chainnode", "xkiro"}
```

Replace `build_bai_provider_slots` tests with `build_xkiro_provider_slots` tests showing one text and one vision slot can share provider name `xkiro` and route identity remains distinct.

Add router-order test with both configured:

```python
router.provider_order(False) == ("chainnode", "xkiro")
router.provider_order(True) == ("chainnode", "xkiro")
router.configured_provider_names(False) == ("chainnode", "xkiro")
router.configured_provider_names(True) == ("chainnode", "xkiro")
```

- [ ] **Step 2: Run and verify failure**

```bash
python -m unittest tests.test_provider_registry -v
```

- [ ] **Step 3: Replace B.AI registry import/factory**

`app/ai/registry.py` becomes conceptually:

```python
from .chainnode import build_chainnode_provider_slots
from .xkiro import build_xkiro_provider_slots

PROVIDER_FACTORIES = {
    "chainnode": build_chainnode_provider_slots,
    "xkiro": build_xkiro_provider_slots,
}
```

Do not modify router scheduling logic.

- [ ] **Step 4: Run registry tests**

```bash
python -m unittest tests.test_provider_registry -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ai/registry.py tests/test_provider_registry.py
git commit -m "feat: register xkiro fallback provider"
```

---

### Task 4: Add the Live xKiro Qualification Probe

**Files:**
- Create: `scripts/probe_xkiro.py`
- Create: `tests/test_xkiro_probe.py`

**Interfaces:**
- Reads `XKIRO_API_KEY`.
- Calls `GET /models` and `POST /chat/completions`.
- CLI accepts explicit `--text-model`, `--vision-model`, optional `--image`, timeout/retry controls.
- Outputs one JSON object per probe event.

- [ ] **Step 1: Write failing offline tests**

Cover:

1. `/models` parser finds an exact model ID.
2. Free hard gate rejects a model unless `access_tier == "free"`.
3. Free hard gate rejects non-zero input or output pricing.
4. Text candidate requires tools capability.
5. Vision candidate additionally requires vision capability.
6. Baseline chat payload contains only `model`, `messages`, `stream` and optional `tools`.
7. `stream` is explicitly `False`.
8. Tool probe fails if the model returns no structured tool call.
9. Tool continuation sends the returned assistant message plus a canonical `role=tool` result.
10. Vision probe sends OpenAI-compatible `image_url` data URL content.
11. HTTP 429 honors `Retry-After` with the same bounded retry behavior used by the retained B.AI probe.

Use mocked HTTP responses only; tests must not need a real API key.

- [ ] **Step 2: Run and verify failure**

```bash
python -m unittest tests.test_xkiro_probe -v
```

- [ ] **Step 3: Implement `scripts/probe_xkiro.py`**

Required probe sequence:

```text
GET /models
  -> exact text model exists
  -> exact vision model exists (if requested)
  -> free/pricing/tool/vision hard gates pass
POST /chat/completions text baseline
POST /chat/completions structured tool call
POST /chat/completions tool continuation
POST /chat/completions vision baseline (when --image provided)
```

Do not maintain a source-code list of free model IDs. Print catalog metadata used for the decision in the JSON record so an operator can audit why a model passed or failed.

- [ ] **Step 4: Run offline probe tests**

```bash
python -m unittest tests.test_xkiro_probe -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/probe_xkiro.py tests/test_xkiro_probe.py
git commit -m "feat: add xkiro compatibility probe"
```

---

### Task 5: Migrate `.env` Safely Away from B.AI

**Files:**
- Modify: `.env.example`
- Modify: `scripts/sync_env.py`
- Modify: `tests/test_provider_env_migration.py`
- Modify: `tests/test_sync_env.py`

**Interfaces:**
- `.env.example` contains Chainnode + xKiro only.
- Existing `.env` B.AI credential/model keys disappear after sync.
- Provider orders containing `bai` are rewritten to valid Chainnode/xKiro orders.

- [ ] **Step 1: Write failing migration tests**

Template assertions:

```text
XKIRO_API_KEY=
XKIRO_TEXT_MODEL=
XKIRO_VISION_MODEL=
XKIRO_REQUEST_TIMEOUT_SEC=60.0
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

and assert no `BAI_API_KEY`, `BAI_TEXT_MODEL`, `BAI_VISION_MODEL`, or `BAI_REQUEST_TIMEOUT_SEC` exists.

Add sync cases:

```text
TEXT_PROVIDER_ORDER=bai                -> chainnode,xkiro
TEXT_PROVIDER_ORDER=chainnode,bai      -> chainnode,xkiro
TEXT_PROVIDER_ORDER=bai,chainnode      -> chainnode,xkiro
TEXT_PROVIDER_ORDER=chainnode          -> chainnode
TEXT_PROVIDER_ORDER=chainnode,bai,foo  -> chainnode,xkiro,foo
```

Mirror for `VISION_PROVIDER_ORDER`.

Assert old B.AI secret/model keys are absent from rendered `.env` and are never copied into `XKIRO_*`.

- [ ] **Step 2: Run and verify failure**

```bash
python -m unittest tests.test_provider_env_migration tests.test_sync_env -v
```

- [ ] **Step 3: Update `.env.example`**

Use:

```env
# ============ AI providers ============
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
VISION_ENABLED=1
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Keep xKiro model defaults empty until live qualification selects exact current free candidates.

- [ ] **Step 4: Add provider-order migration to `sync_env.py`**

Add a pure helper:

```python
def _migrate_provider_order(raw: str) -> str:
    names = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if "bai" not in names:
        return raw

    custom = [name for name in names if name not in {"bai", "chainnode", "xkiro"}]
    ordered = ["chainnode", "xkiro", *custom]
    return ",".join(dict.fromkeys(ordered))
```

Apply it to `TEXT_PROVIDER_ORDER` and `VISION_PROVIDER_ORDER` after legacy-name migration and before template rendering. Preserve an explicit `chainnode`-only order when it contains no `bai`.

- [ ] **Step 5: Run migration tests**

```bash
python -m unittest tests.test_provider_env_migration tests.test_sync_env tests.test_sync_env_permissions -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .env.example scripts/sync_env.py tests/test_provider_env_migration.py tests/test_sync_env.py
git commit -m "feat: migrate provider env from bai to xkiro fallback"
```

---

### Task 6: Remove the B.AI-Only Tool-Choice Workaround

**Files:**
- Modify: `app/ai/base.py`
- Modify: `tests/test_fresh_synthesis_routing.py`
- Modify: `tests/test_provider_local_recovery_retry.py` if it directly asserts the hook.

**Interfaces:**
- `OpenAICompatProvider.chat()` omits `tools` when no tools are active.
- It does not inject `tool_choice=none` solely because a provider-specific flag was set.

- [ ] **Step 1: Repository-search the hook before editing**

Run:

```bash
git grep -n "force_tool_choice_none_when_no_tools\|tool_choice.*none"
```

Expected active-code consumer before deletion: B.AI only. If another active provider uses the hook, stop this task and preserve/generalize it instead of deleting it.

- [ ] **Step 2: Rewrite synthesis regression tests first**

Use a generic `OpenAICompatProvider` or xKiro fixture. Fresh synthesis must prove:

```python
"tools" not in payload
not _has_structured_tool_history(payload)
```

Do not require `payload.get("tool_choice") == "none"`.

- [ ] **Step 3: Run focused tests and verify current mismatch**

```bash
python -m unittest tests.test_fresh_synthesis_routing -v
```

- [ ] **Step 4: Remove the B.AI-only property/branch**

Delete:

```python
self.force_tool_choice_none_when_no_tools = False
```

and:

```python
elif self.force_tool_choice_none_when_no_tools:
    payload["tool_choice"] = "none"
```

only after Step 1 confirms no remaining consumer.

- [ ] **Step 5: Run generic transport/tool tests**

```bash
python -m unittest \
  tests.test_fresh_synthesis_routing \
  tests.test_provider_local_recovery_retry \
  tests.test_tool_budget_synthesis \
  tests.test_tool_fallback_recovery -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/ai/base.py tests/test_fresh_synthesis_routing.py tests/test_provider_local_recovery_retry.py
git commit -m "refactor: remove bai-specific tool choice workaround"
```

---

### Task 7: Delete B.AI Runtime, Probe, Tests, and Guide

**Files:**
- Delete: `app/ai/bai.py`
- Delete: `scripts/probe_bai.py`
- Delete: `tests/test_bai_provider.py`
- Delete: `tests/test_bai_probe.py`
- Delete: `docs/BAI_INTEGRATION.md`

**Interfaces:**
- No active Python import may reference `app.ai.bai`.
- No maintained guide may tell operators to configure B.AI.

- [ ] **Step 1: Run a pre-delete reference scan**

```bash
git grep -n -i "\bb\.ai\b\|\bbai\b\|BAI_" -- ':!docs/superpowers/**'
```

Classify each hit as source, test, maintained docs, env/config, or historical-only.

- [ ] **Step 2: Delete the five B.AI-specific files**

```bash
git rm \
  app/ai/bai.py \
  scripts/probe_bai.py \
  tests/test_bai_provider.py \
  tests/test_bai_probe.py \
  docs/BAI_INTEGRATION.md
```

- [ ] **Step 3: Run import/test collection smoke**

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

Expected at this stage: remaining failures identify active B.AI references that must be repaired in Tasks 8-9; there must be no `ModuleNotFoundError` from overlooked direct imports after those files are adjusted.

- [ ] **Step 4: Commit deletion separately**

```bash
git add -A
git commit -m "refactor: remove bai provider artifacts"
```

---

### Task 8: Update Cross-Provider Routing and Regression Tests

**Files:**
- Modify any active tests identified by the B.AI scan, especially:
  - `tests/test_provider_registry.py`
  - `tests/test_chainnode_provider.py`
  - `tests/test_free_routing.py`
  - `tests/test_fresh_synthesis_routing.py`
  - `tests/test_provider_env_migration.py`
  - router/fallback tests containing literal provider name `bai`

**Interfaces:**
- Generic router tests may use fake names unless testing production configuration.
- Production tests must model `chainnode -> xkiro` fallback.

- [ ] **Step 1: Search remaining active test references**

```bash
git grep -n -i "\bbai\b\|BAI_" -- tests app scripts ':!docs/superpowers/**'
```

Expected after this task: zero hits except migration input fixtures deliberately containing legacy string `bai`.

- [ ] **Step 2: Replace production-fallback test semantics**

Any test that previously asserted `chainnode -> bai` must assert `chainnode -> xkiro`. Preserve the same retry/portable tool-state expectations; only provider identity changes.

For legacy migration tests, `bai` may remain only as input demonstrating that old config is rewritten/removed.

- [ ] **Step 3: Run all AI/provider tests**

```bash
python -m unittest \
  tests.test_xkiro_provider \
  tests.test_xkiro_probe \
  tests.test_chainnode_provider \
  tests.test_provider_registry \
  tests.test_provider_routing \
  tests.test_provider_transport_retry \
  tests.test_provider_retry_state \
  tests.test_provider_retry_health \
  tests.test_provider_portability \
  tests.test_tool_fallback_recovery \
  tests.test_fresh_synthesis_routing \
  tests.test_vision -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests
git commit -m "test: migrate provider regressions to xkiro fallback"
```

---

### Task 9: Rewrite Maintained Runtime Documentation

**Files:**
- Create: `docs/XKIRO.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/CHAINNODE.md`
- Modify: `docs/ENV_SYNC.md`
- Modify any other maintained non-historical document found by the scan.

**Interfaces:**
- Operator setup consistently says Chainnode primary, xKiro fallback.
- xKiro guide explains live model qualification and zero-cost hard gates.

- [ ] **Step 1: Write `docs/XKIRO.md`**

Required sections:

```text
Purpose and fallback role
Endpoint/auth/wire contract
Configuration keys
Why model IDs are explicit settings
How to run scripts/probe_xkiro.py
Free-model qualification gates from /v1/models
Text + vision tool-call qualification procedure
Rollout and rollback
Quota/rate-limit operational notes
Credential safety
```

Document example production order as:

```env
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Do not claim a specific xKiro model is production-qualified until a live probe has actually passed it.

- [ ] **Step 2: Rewrite root/current docs**

README architecture should show:

```text
AIProviderRouter
  -> Chainnode text primary
  -> xKiro text fallback
  -> Chainnode vision primary
  -> xKiro vision fallback
```

`docs/CHAINNODE.md` must retain the already-qualified primary split and replace all `bai` fallback examples with `xkiro`.

`docs/ENV_SYNC.md` must explain that sync removes obsolete `BAI_*` keys and rewrites legacy provider orders containing `bai`.

- [ ] **Step 3: Add/update doc regression assertions**

In `tests/test_provider_env_migration.py` or a dedicated docs test, assert maintained docs contain `XKIRO_API_KEY`, `TEXT_PROVIDER_ORDER=chainnode,xkiro`, and `docs/XKIRO.md`, and do not link to `docs/BAI_INTEGRATION.md`.

- [ ] **Step 4: Run doc/env tests**

```bash
python -m unittest tests.test_provider_env_migration tests.test_sync_env -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md docs .env.example tests/test_provider_env_migration.py
git commit -m "docs: document chainnode primary and xkiro fallback"
```

---

### Task 10: Live Qualify xKiro Candidates Before Deployment

**Files:**
- No source change required unless the selected deployment values are documented after qualification.

**Interfaces:**
- Requires operator-provided `XKIRO_API_KEY` via environment only.

- [ ] **Step 1: Fetch live model catalog with the probe**

Run locally/securely:

```bash
XKIRO_API_KEY='...' python scripts/probe_xkiro.py \
  --text-model '<candidate-text-id>' \
  --vision-model '<candidate-vision-id>' \
  --image '<known-test-image.png>'
```

Never put the key in shell history in production; prefer an already-exported environment variable or secret manager.

- [ ] **Step 2: Require all hard gates**

Text model must pass:

```text
exists in /v1/models
access_tier == free
input price == 0
output price == 0
tools == true
plain chat PASS
structured tool call PASS
tool continuation PASS
```

Vision model must additionally pass:

```text
vision == true
known-image vision answer PASS
```

- [ ] **Step 3: Record selected model IDs in deployment `.env`**

```env
XKIRO_TEXT_MODEL=<qualified-live-model-id>
XKIRO_VISION_MODEL=<qualified-live-model-id>
```

Do not hard-code them into `app/ai/xkiro.py`.

- [ ] **Step 4: Smoke the fallback path deliberately**

In a staging deployment, temporarily make Chainnode unavailable or use a controlled failing Chainnode credential and verify:

```text
text request: chainnode failure -> xkiro success
vision request: chainnode failure -> xkiro success
fallback stats record one real provider transition
tool outputs are not re-executed during rotation
```

Restore valid Chainnode credentials immediately after the smoke.

---

### Task 11: Full Audit, Lint, and B.AI Dead-Reference Gate

**Files:**
- Modify only regressions discovered by this audit.

- [ ] **Step 1: Run full tests**

```bash
python tests/run_tests.py
```

Expected: all tests PASS.

- [ ] **Step 2: Run ruff**

```bash
ruff check app tests scripts
```

Expected: no lint errors.

- [ ] **Step 3: Run active B.AI dead-reference scan**

```bash
git grep -n -i "\bb\.ai\b\|\bbai\b\|BAI_" -- \
  app scripts tests README.md .env.example docs \
  ':!docs/superpowers/**'
```

Expected: only deliberate legacy-migration test fixtures/comments that demonstrate removal of old `bai` values. No runtime import, provider registration, credential setting, deployment example, or maintained guide may treat B.AI as active.

- [ ] **Step 4: Verify provider registry directly**

```bash
python - <<'PY'
from app.ai.registry import PROVIDER_FACTORIES
assert tuple(PROVIDER_FACTORIES) == ("chainnode", "xkiro"), PROVIDER_FACTORIES
print(tuple(PROVIDER_FACTORIES))
PY
```

Expected:

```text
('chainnode', 'xkiro')
```

- [ ] **Step 5: Verify default order directly**

```bash
python - <<'PY'
from app.config import Settings
s = Settings(_env_file=None)
assert s.text_provider_order_list == ["chainnode", "xkiro"]
assert s.vision_provider_order_list == ["chainnode", "xkiro"]
print(s.text_provider_order_list, s.vision_provider_order_list)
PY
```

- [ ] **Step 6: Final commit for audit fixes if needed**

```bash
git add -A
git commit -m "fix: close xkiro migration audit findings"
```

Skip this commit only if Steps 1-5 required no changes.

---

## Deployment Sequence

1. Merge code with Chainnode still configured and working.
2. Run `python scripts/sync_env.py` on the host; confirm old `BAI_*` keys disappear and provider orders become valid.
3. Set `XKIRO_API_KEY` plus live-qualified `XKIRO_TEXT_MODEL` and `XKIRO_VISION_MODEL`.
4. Confirm Chainnode production values remain:

```env
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

5. Rebuild/restart the bot.
6. Smoke normal text and vision requests and verify Chainnode answers first.
7. Perform a controlled fallback smoke and verify xKiro takes over.
8. Restore Chainnode and monitor provider answer/fallback metrics.

## Rollback

Because B.AI is intentionally deleted, rollback does not mean switching back to B.AI. Operational rollback is:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

This disables xKiro fallback without changing Chainnode. A source rollback to a pre-migration release is required only if the xKiro integration itself causes runtime regressions outside fallback selection.
