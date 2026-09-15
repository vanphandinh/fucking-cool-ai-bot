# Chainnode Split Text/Vision Live Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a production-safe Chainnode vision slot so live traffic uses `cl/cline-free/deepseek-v4.1-flash` for text and `cl/cline-free/muse-spark-1.3-contributor` for vision, with B.AI retained as fallback for both routes.

**Architecture:** Keep the existing provider-agnostic router and add a second Chainnode slot keyed by the existing `(provider name, route)` capability model. Text and vision remain independently configurable and independently enabled by `TEXT_PROVIDER_ORDER` and `VISION_PROVIDER_ORDER`; Chainnode vision stays opt-in, while the repository default remains B.AI-only. Use the already-qualified OpenAI-compatible multimodal payload and explicit `stream=false` transport contract.

**Tech Stack:** Python 3.11/3.12, Pydantic Settings, httpx, unittest, Docker Compose, existing OpenAI-compatible provider/router abstractions.

**Spec:** `docs/CHAINNODE.md` (current Chainnode runtime contract), `scripts/probe_chainnode_vision.py` (qualified vision wire contract), and the approved 2026-09-15 deployment decision: DeepSeek V4.1 Flash for text, Muse Spark 1.3 Contributor for vision, B.AI fallback.

## Global Constraints

- Production text model: `cl/cline-free/deepseek-v4.1-flash`.
- Production vision model: `cl/cline-free/muse-spark-1.3-contributor`.
- Production provider order after canary: `TEXT_PROVIDER_ORDER=chainnode,bai` and `VISION_PROVIDER_ORDER=chainnode,bai`.
- Repository defaults remain rollback-safe: `TEXT_PROVIDER_ORDER=bai`, `VISION_PROVIDER_ORDER=bai`, and blank Chainnode model settings.
- Chainnode transport continues to send explicit `stream=false` in runtime requests.
- Chainnode vision max images is initially `1`; do not expand multi-image support in this change.
- Do not add model rotation, adaptive scoring, quota tracking, latency benchmarking, or new dependencies.
- `VISION_ENABLED=0` remains the global emergency switch for image requests.
- API keys remain environment-only and must never be committed, logged, or passed as command-line values.

---

## File Structure

- Modify `app/config.py` — add `CHAINNODE_VISION_MODEL` and route-specific validation.
- Modify `app/ai/chainnode.py` — create independent text and vision Chainnode slots.
- Modify `tests/test_chainnode_provider.py` — unit and wire-contract tests for split models/routes.
- Modify `tests/test_chainnode_audit.py` — integration fallback regression for Chainnode vision -> B.AI vision and secret-scan coverage for the new probe.
- Modify `tests/test_provider_env_migration.py` — template/sync regressions for the new environment key and provider order.
- Modify `.env.example` — document the new vision model setting while keeping B.AI defaults.
- Modify `docs/CHAINNODE.md` — replace text-only language with qualified split-route configuration, canary, rollback, and smoke-test instructions.
- Keep `scripts/probe_chainnode_vision.py` and `tests/test_chainnode_vision_probe.py` unchanged except for fixes discovered by verification.

---

### Task 1: Add route-specific Chainnode configuration

**Files:**
- Modify: `app/config.py`
- Modify: `tests/test_chainnode_provider.py`
- Modify: `tests/test_provider_env_migration.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: existing `Settings.text_provider_order_list` and `Settings.vision_provider_order_list`.
- Produces: `Settings.chainnode_vision_model: str`, environment key `CHAINNODE_VISION_MODEL`, and route-specific startup validation.

- [ ] **Step 1: Write failing configuration tests**

Add tests that require a blank Chainnode vision model by default, require the model only when Chainnode vision is actually selected, and preserve the B.AI-only default:

```python
def test_chainnode_defaults_keep_vision_bai_only(self) -> None:
    settings = Settings(_env_file=None)
    self.assertEqual(settings.chainnode_vision_model, "")
    self.assertEqual(settings.vision_provider_order_list, ["bai"])


def test_chainnode_vision_order_requires_explicit_model(self) -> None:
    with self.assertRaisesRegex(ValueError, "CHAINNODE_VISION_MODEL"):
        Settings(
            _env_file=None,
            chainnode_api_key="c",
            vision_enabled=True,
            vision_provider_order="chainnode,bai",
        )


def test_disabled_vision_does_not_require_chainnode_vision_model(self) -> None:
    settings = Settings(
        _env_file=None,
        chainnode_api_key="c",
        vision_enabled=False,
        vision_provider_order="chainnode,bai",
    )
    self.assertFalse(settings.vision_enabled)
```

Update the env-template regression to assert:

```python
self.assertIn("CHAINNODE_VISION_MODEL=\n", template)
self.assertIn("VISION_PROVIDER_ORDER=bai", template)
```

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python -m unittest \
  tests.test_chainnode_provider.ChainnodeDeploymentConfigTests \
  tests.test_provider_env_migration.ProviderEnvMigrationTests -v
```

Expected: FAIL because `Settings` does not yet define `chainnode_vision_model` and `.env.example` lacks `CHAINNODE_VISION_MODEL`.

- [ ] **Step 3: Implement the minimal settings change**

In `Settings`, add the new field beside the existing Chainnode model settings:

```python
chainnode_text_model: str = ""
chainnode_vision_model: str = ""
```

Extend `_check_provider_and_timeout_config()` with route-specific validation:

```python
if (
    self.vision_enabled
    and self.chainnode_api_key.strip()
    and "chainnode" in self.vision_provider_order_list
    and not self.chainnode_vision_model.strip()
):
    raise ValueError(
        "CHAINNODE_VISION_MODEL là bắt buộc khi Chainnode được bật "
        "trong VISION_PROVIDER_ORDER"
    )
```

Add to `.env.example` under the Chainnode text model:

```env
# Qualified separately from the text route; required only when Chainnode is in VISION_PROVIDER_ORDER.
CHAINNODE_VISION_MODEL=
```

Do not change the default provider orders in `.env.example`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same unittest command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py .env.example tests/test_chainnode_provider.py tests/test_provider_env_migration.py
git commit -m "feat: add Chainnode vision model config"
```

---

### Task 2: Build independent Chainnode text and vision provider slots

**Files:**
- Modify: `app/ai/chainnode.py`
- Modify: `tests/test_chainnode_provider.py`

**Interfaces:**
- Consumes: `Settings.chainnode_text_model`, `Settings.chainnode_vision_model`, `text_provider_order_list`, `vision_provider_order_list`, and `vision_enabled`.
- Produces: one Chainnode `route="text"` slot and/or one Chainnode `route="vision"` slot, both using the shared `OpenAICompatProvider`.

- [ ] **Step 1: Write failing provider-slot tests**

Add tests for independent model selection and slot construction:

```python
def test_text_and_vision_factories_use_distinct_models(self) -> None:
    settings = SimpleNamespace(
        chainnode_api_key="secret",
        chainnode_base_url="https://dn.chainno.de/v1",
        chainnode_text_model="cl/cline-free/deepseek-v4.1-flash",
        chainnode_vision_model="cl/cline-free/muse-spark-1.3-contributor",
        chainnode_request_timeout_sec=25.0,
    )
    text = chainnode.make_chainnode_provider(settings, vision=False)
    vision = chainnode.make_chainnode_provider(settings, vision=True)
    try:
        self.assertEqual(text.model, "cl/cline-free/deepseek-v4.1-flash")
        self.assertEqual(text.capabilities.route, "text")
        self.assertFalse(text.capabilities.supports_vision)
        self.assertEqual(text.capabilities.max_images, 0)

        self.assertEqual(vision.model, "cl/cline-free/muse-spark-1.3-contributor")
        self.assertEqual(vision.capabilities.route, "vision")
        self.assertTrue(vision.capabilities.supports_vision)
        self.assertEqual(vision.capabilities.max_images, 1)
    finally:
        asyncio.run(text.aclose())
        asyncio.run(vision.aclose())
```

Add a router construction regression:

```python
def test_chainnode_builds_selected_text_and_vision_slots_independently(self) -> None:
    router = build_provider_router(
        Settings(
            _env_file=None,
            chainnode_api_key="c",
            chainnode_text_model="cl/cline-free/deepseek-v4.1-flash",
            chainnode_vision_model="cl/cline-free/muse-spark-1.3-contributor",
            bai_api_key="b",
            text_provider_order="chainnode,bai",
            vision_provider_order="chainnode,bai",
        )
    )
    try:
        chainnode_slots = [p for p in router.providers if p.name == "chainnode"]
        by_route = {p.capabilities.route: p for p in chainnode_slots}
        self.assertEqual(set(by_route), {"text", "vision"})
        self.assertEqual(
            by_route["text"].model,
            "cl/cline-free/deepseek-v4.1-flash",
        )
        self.assertEqual(
            by_route["vision"].model,
            "cl/cline-free/muse-spark-1.3-contributor",
        )
    finally:
        asyncio.run(_close_router(router))
```

Also add a vision-only selection case proving Chainnode vision can be enabled even when Chainnode text is not selected.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
python -m unittest tests.test_chainnode_provider -v
```

Expected: FAIL because `make_chainnode_provider()` has no `vision` argument and `build_chainnode_provider_slots()` only creates the text route.

- [ ] **Step 3: Implement the minimal provider split**

Refactor the factory to choose model/capabilities by route while preserving the current transport contract:

```python
def make_chainnode_provider(
    settings: Settings,
    *,
    name: str = "chainnode",
    vision: bool = False,
) -> OpenAICompatProvider:
    model = str(
        settings.chainnode_vision_model if vision else settings.chainnode_text_model
    ).strip()
    setting_name = "CHAINNODE_VISION_MODEL" if vision else "CHAINNODE_TEXT_MODEL"
    if not model:
        raise ValueError(f"{setting_name} là bắt buộc khi khởi tạo Chainnode provider")

    return OpenAICompatProvider(
        name=name,
        base_url=settings.chainnode_base_url,
        api_key=settings.chainnode_api_key,
        model=model,
        timeout=settings.chainnode_request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="vision" if vision else "text",
            supports_vision=vision,
            max_images=1 if vision else 0,
        ),
        explicit_stream=False,
    )
```

Build slots independently:

```python
def build_chainnode_provider_slots(settings: Settings) -> list[AIProvider]:
    if not settings.chainnode_api_key:
        return []

    slots: list[AIProvider] = []
    if "chainnode" in settings.text_provider_order_list and settings.chainnode_text_model:
        slots.append(make_chainnode_provider(settings))
    if (
        settings.vision_enabled
        and "chainnode" in settings.vision_provider_order_list
        and settings.chainnode_vision_model
    ):
        slots.append(make_chainnode_provider(settings, vision=True))
    return slots
```

Do not add a hard-coded model allowlist; live qualification remains the gate for Chainnode model IDs.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_chainnode_provider -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ai/chainnode.py tests/test_chainnode_provider.py
git commit -m "feat: add Chainnode vision provider slot"
```

---

### Task 3: Prove the production multimodal wire contract and fallback path

**Files:**
- Modify: `tests/test_chainnode_provider.py`
- Modify: `tests/test_chainnode_audit.py`

**Interfaces:**
- Consumes: Chainnode vision slot from Task 2 and the existing router capability filter.
- Produces: regressions proving image payload preservation, explicit non-stream requests, and `chainnode -> bai` vision fallback.

- [ ] **Step 1: Write a failing multimodal wire-contract test**

Create a Chainnode vision provider with Muse and mock its HTTP transport. Send a runtime-shaped multimodal message:

```python
messages = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "Describe this image"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aGVsbG8="},
            },
        ],
    }
]
```

Assert the outgoing request keeps:

```python
self.assertEqual(requests[0]["model"], "cl/cline-free/muse-spark-1.3-contributor")
self.assertIs(requests[0]["stream"], False)
self.assertEqual(requests[0]["messages"], messages)
```

- [ ] **Step 2: Write a failing vision fallback integration test**

Build a router with Chainnode and B.AI vision slots. Mock Chainnode vision to return HTTP 503 and B.AI vision to return a normal assistant answer. Invoke:

```python
result = await router.complete(
    messages,
    None,
    _noop_tool,
    requires_vision=True,
    image_count=1,
)
```

Assert:

```python
self.assertEqual(result.provider, "bai")
self.assertEqual(result.content, "vision fallback ok")
self.assertEqual(result.fallbacks, ("bai",))
```

Use `(provider.name, provider.capabilities.route)` as the test lookup key because text and vision slots may share the same provider name.

- [ ] **Step 3: Run focused tests and verify RED where appropriate**

Run:

```bash
python -m unittest \
  tests.test_chainnode_provider \
  tests.test_chainnode_audit -v
```

Expected before Task 2 implementation: the vision-specific tests fail because no Chainnode vision slot exists. After Task 2 is green, these tests should pass without router changes; if they do not, fix only the smallest provider/router compatibility defect demonstrated by the failing test.

- [ ] **Step 4: Extend the secret-scan regression**

Add `scripts/probe_chainnode_vision.py` to `CHAINNODE_FILES` in `tests/test_chainnode_audit.py` so both Chainnode probe scripts remain covered by the no-embedded-secret check.

- [ ] **Step 5: Re-run focused tests**

Run the unittest command from Step 3. Expected: PASS with no duplicate-slot error and no secret-scan failure.

- [ ] **Step 6: Commit**

```bash
git add tests/test_chainnode_provider.py tests/test_chainnode_audit.py
git commit -m "test: cover Chainnode vision routing and fallback"
```

---

### Task 4: Update operator configuration and deployment documentation

**Files:**
- Modify: `.env.example`
- Modify: `docs/CHAINNODE.md`
- Modify: `tests/test_provider_env_migration.py`

**Interfaces:**
- Consumes: the runtime settings introduced in Tasks 1-3.
- Produces: an operator-facing live configuration and deterministic rollback procedure.

- [ ] **Step 1: Write failing documentation/template assertions**

Extend `test_runtime_docs_match_ai_timeout_defaults` or add a focused Chainnode vision docs test requiring these exact strings in `docs/CHAINNODE.md`:

```text
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
TEXT_PROVIDER_ORDER=chainnode,bai
VISION_PROVIDER_ORDER=chainnode,bai
```

Also require that the old statement saying Chainnode is text-only is absent.

- [ ] **Step 2: Run the docs/env tests and verify RED**

Run:

```bash
python -m unittest tests.test_provider_env_migration -v
```

Expected: FAIL because `docs/CHAINNODE.md` still documents Chainnode as text-only.

- [ ] **Step 3: Update `docs/CHAINNODE.md`**

Document the qualified split explicitly:

```env
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
TEXT_PROVIDER_ORDER=chainnode,bai
VISION_PROVIDER_ORDER=chainnode,bai
```

Document qualification evidence from the live probe:

- Muse: non-stream vision PASS, stream PASS, structured vision tool call PASS, continuation PASS.
- DeepSeek V4.1: retained as text primary; it also passed vision qualification but is intentionally not used for vision to preserve per-model Cline quota.
- GLM 5.3 Flash: qualified candidate only, not in production routing.

Document rollback in this order:

```env
VISION_PROVIDER_ORDER=bai
```

Restart the bot; text remains on Chainnode. If all image handling must be stopped immediately:

```env
VISION_ENABLED=0
```

- [ ] **Step 4: Verify env sync behavior with the new key**

Add a test fixture where an existing `.env` lacks `CHAINNODE_VISION_MODEL`, run `scripts/sync_env.py`, and assert:

```python
self.assertIn("CHAINNODE_VISION_MODEL=", rendered)
self.assertIn("CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash", rendered)
self.assertIn("TEXT_PROVIDER_ORDER=chainnode,bai", rendered)
```

This proves deployment can introduce the new key without overwriting existing production values.

- [ ] **Step 5: Run docs/env tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_provider_env_migration -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add .env.example docs/CHAINNODE.md tests/test_provider_env_migration.py
git commit -m "docs: document Chainnode split text vision rollout"
```

---

### Task 5: Full offline verification and PR gate

**Files:**
- No planned production-code changes; fix only failures caused by this branch.

**Interfaces:**
- Consumes: completed implementation from Tasks 1-4.
- Produces: a reviewable branch with all repository gates green before live deployment.

- [ ] **Step 1: Run Chainnode-focused tests**

```bash
python -m unittest \
  tests.test_chainnode_provider \
  tests.test_chainnode_probe \
  tests.test_chainnode_vision_probe \
  tests.test_chainnode_audit \
  tests.test_provider_env_migration -v
```

Expected: all PASS.

- [ ] **Step 2: Run the full offline suite**

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: zero failures/errors.

- [ ] **Step 3: Run static/build checks**

```bash
python -m ruff check .
python -m compileall -q app tests scripts
docker compose config >/dev/null
docker build --tag fcai-chainnode-vision-test .
```

Expected: all commands exit `0`.

- [ ] **Step 4: Re-run the live vision compatibility probe from a networked host**

Use the branch HEAD and environment-only API key:

```bash
read -s CHAINNODE_API_KEY
export CHAINNODE_API_KEY
python scripts/probe_chainnode_vision.py \
  --models cl/cline-free/muse-spark-1.3-contributor,cl/cline-free/deepseek-v4.1-flash
```

Required gate:

- `models`: compatible `true`, both models found.
- Muse `nonstream_vision`: compatible `true`, exact `47-GREEN-CIRCLE`.
- Muse `vision_tool_call`: compatible `true`.
- Muse `vision_tool_continuation`: compatible `true`.
- DeepSeek `nonstream_vision`: compatible `true` as a regression check.

- [ ] **Step 5: Open a PR from `probe/chainnode-vision-models` to `main`**

PR summary must state that repository defaults remain B.AI-only and live Chainnode vision activation is a separate environment rollout after merge.

- [ ] **Step 6: Require GitHub Actions green on Python 3.11 and 3.12 before merge**

Do not merge on local-only evidence. Verify the final PR head SHA has successful audit workflow jobs and the production Docker build gate.

---

### Task 6: Production rollout with no-change deploy first, then opt-in vision canary

**Files:**
- VPS `.env` only after the code is merged and deployed.

**Interfaces:**
- Consumes: merged runtime support and current production secrets.
- Produces: live text routing to DeepSeek V4.1 and live vision routing to Muse, both with B.AI fallback.

- [ ] **Step 1: Snapshot current production state before changing anything**

On the VPS:

```bash
git rev-parse HEAD
cp .env ".env.pre-chainnode-vision-$(date +%Y%m%d-%H%M%S)"
docker compose ps
```

Record the current commit SHA and preserve the env backup with owner-only permissions.

- [ ] **Step 2: Deploy the merged code without changing provider order**

Pull the merged `main`, then sync the environment template:

```bash
git checkout main
git pull --ff-only origin main
python scripts/sync_env.py
chmod 600 .env
docker compose build bot
docker compose up -d bot
```

At this stage keep:

```env
VISION_PROVIDER_ORDER=bai
```

This proves the new code can run in production with the old vision behavior before activation.

- [ ] **Step 3: Smoke test the no-change deployment**

Verify:

1. One normal text question succeeds.
2. One existing image-understanding request succeeds through B.AI.
3. Bot logs show no startup validation exception and no duplicate provider slot exception.

If any check fails, restore the previous commit/env backup before attempting Chainnode vision activation.

- [ ] **Step 4: Set the final live split configuration**

Edit only these routing/model values in `.env` while preserving secrets and timeout values:

```env
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
TEXT_PROVIDER_ORDER=chainnode,bai
VISION_PROVIDER_ORDER=chainnode,bai
VISION_ENABLED=1
```

Do not change `CHAINNODE_REQUEST_TIMEOUT_SEC`, B.AI models, retry budgets, or question timeouts in the same rollout.

- [ ] **Step 5: Restart only the bot service**

```bash
docker compose up -d --force-recreate bot
docker compose ps bot
```

- [ ] **Step 6: Run live user-path smoke tests**

Run these through the real Telegram bot:

1. **Text/no tool:** a simple factual or arithmetic prompt; expect a normal answer.
2. **Text/tool:** a current-information prompt that requires `web_search`; expect tool completion and final synthesis.
3. **Vision/simple:** upload an image with clearly visible text/object and ask the bot to identify it; expect a correct answer.
4. **Vision + tool-capable conversation:** upload an image and ask a question that may require a follow-up web lookup; verify the request completes without malformed tool markup or duplicate tool execution.
5. **Fallback health:** inspect logs for `AI provider chainnode lỗi`; if natural 429/5xx occurs, verify B.AI completes the affected request rather than failing the whole user request.

- [ ] **Step 7: Observe the canary window before declaring rollout complete**

For the first production window, watch bot logs for:

```bash
docker compose logs --since=30m bot | grep -E 'chainnode|bai|HTTP 429|HTTP 5[0-9][0-9]|ReadTimeout|ConnectTimeout|NoCapableProvider|AllProvidersFailed'
```

Acceptance criteria:

- No startup/config validation errors.
- No `NoCapableProvider` for one-image requests.
- No systematic Chainnode vision HTTP 4xx caused by payload shape.
- Natural Chainnode failures fall back to B.AI and terminate within existing retry budgets.
- Text route remains functional while vision traffic uses the separate Muse model quota.

- [ ] **Step 8: Roll back vision independently if needed**

First-line rollback:

```env
VISION_PROVIDER_ORDER=bai
```

Then:

```bash
docker compose up -d --force-recreate bot
```

This leaves Chainnode text on DeepSeek untouched. If image handling itself must be disabled:

```env
VISION_ENABLED=0
```

If the new runtime code causes a broader regression, restore the pre-rollout `.env` backup and deploy the recorded previous commit.

---

## Final Verification Checklist

- [ ] `CHAINNODE_TEXT_MODEL` and `CHAINNODE_VISION_MODEL` are independent settings.
- [ ] Chainnode text slot is `route="text"`, `supports_vision=False`, `max_images=0`.
- [ ] Chainnode vision slot is `route="vision"`, `supports_vision=True`, `max_images=1`.
- [ ] Runtime Chainnode calls still force `stream=false`.
- [ ] Router can select Chainnode vision without Chainnode text and vice versa.
- [ ] One-image Chainnode vision failure falls back to B.AI vision.
- [ ] Repository defaults remain B.AI-only.
- [ ] `sync_env.py` adds the new key without overwriting existing live values.
- [ ] Vision live probe passes on Muse after the final branch changes.
- [ ] Full tests, Ruff, compileall, Docker Compose validation, and Docker build pass.
- [ ] GitHub Actions are green on the final PR head.
- [ ] No-change production deploy is smoke-tested before enabling Chainnode vision.
- [ ] Live config is DeepSeek text + Muse vision + B.AI fallback.
- [ ] Vision-only rollback is documented and verified operationally.
