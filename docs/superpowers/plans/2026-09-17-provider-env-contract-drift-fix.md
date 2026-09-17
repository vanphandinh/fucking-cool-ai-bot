# Provider Env Contract Drift Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining blocking spec drift by making the canonical design contract match the deployed Chainnode model defaults, and add a regression guard that prevents the design spec and `.env.example` provider/retry defaults from diverging again.

**Architecture:** Keep runtime and deployment behavior unchanged. Treat `.env.example` as the concrete deployed default schema and make the implemented design contract describe those same defaults: Chainnode keeps its qualified text/vision defaults, while xKiro model pools remain blank until live qualification. Add a focused unittest that extracts the canonical `env` blocks from the design spec and compares the exact provider/retry key/value contract with `.env.example`.

**Tech Stack:** Python 3.11/3.12, `unittest`, Markdown, Ruff, pip-audit, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md`

## Global Constraints

- Provider runtime remains plural-only.
- `XKIRO_DEFAULT_BASE_URL` remains the shared runtime/probe default endpoint constant.
- Provider order remains `chainnode,xkiro` for text and vision.
- Retry/recovery defaults remain `2`, `3`, `5`, and `5` for consecutive, per-provider, per-request, and recovery-hop limits respectively.
- `scripts/sync_env.py` remains strict: no migration, inference, renaming, or cross-key copying.
- Do not change provider selection policy, timeout values, target ordering, retry semantics, or recovery semantics.
- Do not hard-code xKiro model IDs.
- `.env.example` remains the complete supported deployment key schema.
- The final branch HEAD must pass Ruff, dependency checks, compileall, full unittest discovery, dependency audit, Docker Compose validation, production image build, and GitHub Actions on Python 3.11 and 3.12.
- Final review must have zero blocking Standards or Spec findings.

## File Map

- Modify `tests/test_provider_env_contract.py`: add a design-spec/template parity regression and explicitly pin the recovery-hop environment default.
- Modify `docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md`: publish the same qualified Chainnode defaults already used by `.env.example` and active operator docs, and document the intentional Chainnode/xKiro default asymmetry.
- Do not modify runtime/provider code, `scripts/sync_env.py`, `.env.example`, `README.md`, `docs/CHAINNODE.md`, `docs/XKIRO.md`, or `docs/ENV_SYNC.md`; at the planning baseline they already describe the intended runtime/deployment behavior.

---

### Task 1: Lock the canonical provider/retry contract and fix the design drift

**Files:**
- Modify: `tests/test_provider_env_contract.py`
- Modify: `docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md`

**Interfaces:**
- Consumes: `_assignments(text: str) -> dict[str, str]` already defined in `tests/test_provider_env_contract.py`.
- Produces: `_design_contract_assignments() -> dict[str, str]`, a test-only parser for the canonical provider/retry `env` blocks in the design spec.
- Produces: `ProviderEnvContractTests.test_design_contract_matches_template_provider_and_retry_defaults`, which prevents the design contract from drifting from `.env.example`.

- [ ] **Step 1: Add the failing design/template parity regression**

Add `import re` with the existing imports, define the design-spec path next to `ROOT`, then add the helper and test below.

```python
import re

ROOT = Path(__file__).parents[1]
DESIGN_SPEC = (
    ROOT
    / "docs"
    / "superpowers"
    / "specs"
    / "2026-09-17-canonical-env-cutover-design.md"
)
SYNC_SCRIPT = ROOT / "scripts" / "sync_env.py"


def _design_contract_assignments() -> dict[str, str]:
    text = DESIGN_SPEC.read_text(encoding="utf-8")
    section = text.split("## Canonical environment contract", 1)[1]
    section = section.split("## Strict environment synchronization", 1)[0]
    blocks = re.findall(r"```env\n(.*?)```", section, flags=re.DOTALL)
    if len(blocks) != 2:
        raise AssertionError(
            "canonical environment contract must contain provider and retry env blocks"
        )

    assignments: dict[str, str] = {}
    for block in blocks:
        assignments.update(_assignments(block))
    return assignments
```

Extend `test_template_publishes_canonical_provider_and_retry_contract` with explicit current defaults:

```python
self.assertEqual(
    assignments["CHAINNODE_TEXT_MODELS"],
    "cl/cline-free/deepseek-v4.1-flash",
)
self.assertEqual(
    assignments["CHAINNODE_VISION_MODELS"],
    "cl/cline-free/muse-spark-1.3-contributor",
)
self.assertEqual(assignments["PROVIDER_RECOVERY_MAX_HOPS_PER_REQUEST"], "5")
```

Add this test to `ProviderEnvContractTests`:

```python
def test_design_contract_matches_template_provider_and_retry_defaults(self) -> None:
    template = _assignments((ROOT / ".env.example").read_text(encoding="utf-8"))
    design = _design_contract_assignments()
    keys = (
        "CHAINNODE_API_KEYS",
        "CHAINNODE_BASE_URL",
        "CHAINNODE_TEXT_MODELS",
        "CHAINNODE_VISION_MODELS",
        "CHAINNODE_REQUEST_TIMEOUT_SEC",
        "XKIRO_API_KEYS",
        "XKIRO_BASE_URL",
        "XKIRO_TEXT_MODELS",
        "XKIRO_VISION_MODELS",
        "XKIRO_REQUEST_TIMEOUT_SEC",
        "TEXT_PROVIDER_ORDER",
        "VISION_PROVIDER_ORDER",
        "PROVIDER_RETRY_MAX_CONSECUTIVE",
        "PROVIDER_RETRY_MAX_PER_PROVIDER",
        "PROVIDER_RETRY_MAX_PER_REQUEST",
        "PROVIDER_RECOVERY_MAX_HOPS_PER_REQUEST",
    )

    self.assertEqual(
        {key: design[key] for key in keys},
        {key: template[key] for key in keys},
    )
```

- [ ] **Step 2: Run the focused contract tests and verify the new test is red**

Run:

```bash
python -m unittest discover -s tests -p 'test_provider_env_contract.py' -v
```

Expected: the newly added design/template parity test fails because the design spec currently has blank `CHAINNODE_TEXT_MODELS` and `CHAINNODE_VISION_MODELS`, while `.env.example` publishes the qualified Chainnode defaults. Existing provider/runtime assertions should remain green.

- [ ] **Step 3: Correct the implemented design contract**

In `docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md`, change only the two Chainnode model lines in the canonical provider configuration block to:

```env
CHAINNODE_TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
```

Immediately after the provider configuration block, add this paragraph:

```markdown
Chainnode ships qualified text and vision model defaults in the canonical deployment template so a configured Chainnode credential pool is operational without an additional model-selection step. xKiro model pools intentionally remain blank until current candidates pass live qualification; the runtime does not hard-code xKiro model IDs.
```

In the `## Tests` coverage list, add this bullet:

```markdown
- canonical provider/retry defaults in the implemented design contract stay value-aligned with `.env.example`;
```

Do not change runtime defaults, `.env.example`, provider factories, provider order, retry/recovery values, or xKiro model configuration.

- [ ] **Step 4: Re-run the focused contract tests and verify green**

Run:

```bash
python -m unittest discover -s tests -p 'test_provider_env_contract.py' -v
```

Expected: all tests in `test_provider_env_contract.py` pass.

- [ ] **Step 5: Cross-check active documentation remains aligned**

Run:

```bash
rg -n \
  'CHAINNODE_TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash|CHAINNODE_VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor' \
  .env.example README.md docs/CHAINNODE.md docs/ENV_SYNC.md \
  docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md
```

Expected: both qualified Chainnode defaults appear consistently in the deployment template, active operator documentation, and the implemented design contract.

Then run:

```bash
rg -n '^XKIRO_(TEXT|VISION)_MODELS=$' \
  .env.example docs/XKIRO.md \
  docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md
```

Expected: xKiro text/vision model pools remain blank in the canonical template/spec examples that define defaults.

- [ ] **Step 6: Commit the self-contained fix**

```bash
git add \
  tests/test_provider_env_contract.py \
  docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md
git commit -m "docs: align canonical env design contract"
```

---

### Task 2: Run the whole-branch acceptance gate

**Files:**
- Verify only; no additional source changes are expected.

**Interfaces:**
- Consumes: the Task 1 commit and repository CI workflow `.github/workflows/audit.yml`.
- Produces: a final branch HEAD with green local/CI verification and zero blocking final-review findings.

- [ ] **Step 1: Run static and dependency checks**

Run:

```bash
python -m ruff check .
python -m pip check
python -m compileall -q app tests scripts
```

Expected: all commands exit `0`.

- [ ] **Step 2: Run the full offline regression suite**

Run:

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: both commands exit `0` with no failed/error tests.

- [ ] **Step 3: Run dependency audit**

Run:

```bash
python -m pip_audit --progress-spinner off
```

Expected: exit `0` with no actionable vulnerable installed dependency findings under the repository's pinned/allowed dependency set.

- [ ] **Step 4: Validate Docker Compose and the production image in an isolated worktree**

Only run this in the isolated execution worktree, where no deployment `.env` exists.

```bash
test ! -e .env
cp .env.example .env
docker compose config --quiet
docker build --tag fcai-audit:provider-env-contract-fix .
rm .env
```

Expected: Compose validation and image build both succeed; the temporary `.env` is removed afterward.

- [ ] **Step 5: Push and require GitHub Actions green on the exact final HEAD**

```bash
git push origin feat/provider-env-cleanup
git rev-parse HEAD
```

Use the GitHub connector to inspect the `Audit checks` run whose `head_sha` exactly equals the printed SHA.

Expected:

```text
tests (3.11): success
tests (3.12): success
Python 3.12 Validate Docker Compose: success
Python 3.12 Build production image: success
```

Do not accept a green run from an earlier commit.

- [ ] **Step 6: Run final branch review**

Invoke the `code-review` skill against `main...feat/provider-env-cleanup`, using the implemented design contract as the Spec source.

Expected: zero blocking Standards findings and zero blocking Spec findings. In particular, the previous Chainnode model-default drift must no longer reproduce because the design/template parity regression is green.

- [ ] **Step 7: Record the final completion evidence**

Capture the final commit SHA, GitHub Actions run ID, focused contract-test result, full unittest result, Docker validation/build result, and final code-review summary in the implementation chat before declaring the branch ready.
