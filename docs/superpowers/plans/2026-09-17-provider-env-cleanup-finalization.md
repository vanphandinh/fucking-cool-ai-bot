# Provider Env Cleanup Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining canonical-environment acceptance gap by adding an automated whole-tree retired-vocabulary gate, proving the final branch HEAD passes every documented acceptance check, and handing the branch off for review/integration without expanding scope.

**Architecture:** Add one focused repository scanner under `scripts/` and a focused unittest module that proves boundary matching, plural-name safety, repository scanning, and clean-tree behavior. Wire the scanner into the existing `Audit checks` workflow so the requirement is visible as an explicit CI step as well as covered by the full test suite. Do not change provider selection, retry semantics, timeout values, model IDs, or the duplicate full-suite invocation in this branch.

**Tech Stack:** Python 3.11/3.12, `unittest`, stdlib `pathlib`/`re`/`subprocess`, Ruff 0.16.6, pip-audit 2.10.1, Docker Compose, Docker, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md`

## Global Constraints

- Runtime/helper scripts must contain only the canonical environment contract.
- `.env.example` must contain only current names.
- Active and retained docs must contain only current environment guidance.
- Current tests must describe canonical behavior without superseded compatibility fixtures.
- A whole-tree scan must find no retired environment/provider vocabulary.
- Ruff, dependency checks, compileall, full unittest discovery, and dependency audit must pass.
- Docker Compose validation and the production image build must pass.
- GitHub Actions for the exact final branch HEAD must be green on Python 3.11 and 3.12.
- Final branch review must have zero blocking Standards or Spec findings.
- Do not rewrite Git history.
- Do not change provider selection policy beyond the canonical environment boundary.
- Do not add a migration utility or compatibility shim.
- Do not hard-code xKiro model IDs.
- Do not alter retry limits, timeout values, provider target ordering, or recovery semantics beyond canonical setting names.
- Do not remove the duplicate full-suite invocation from `.github/workflows/audit.yml` in this branch; track that as a separate non-blocking cleanup after this branch is integrated.

---

## File Structure

- Create `scripts/check_canonical_env_vocabulary.py`: single-purpose tracked-file scanner and CLI exit gate.
- Create `tests/test_canonical_env_vocabulary.py`: unit tests for scanner behavior plus a repository-level regression test.
- Modify `.github/workflows/audit.yml`: add an explicit canonical-vocabulary gate; otherwise preserve existing CI behavior.
- No unconditional provider/runtime/doc content changes are planned. If the new repository-level regression test reports a real hit, stop execution and amend this plan with the exact emitted path(s) before editing them; do not make ad-hoc cleanup changes outside reviewed scope.

---

### Task 1: Add a boundary-safe canonical vocabulary scanner

**Files:**
- Create: `scripts/check_canonical_env_vocabulary.py`
- Create: `tests/test_canonical_env_vocabulary.py`

**Interfaces:**
- Produces: `retired_identifiers() -> tuple[str, ...]`
- Produces: `scan_text(text: str) -> tuple[str, ...]`
- Produces: `tracked_files(root: Path) -> tuple[Path, ...]`
- Produces: `scan_repository(root: Path) -> tuple[str, ...]`
- Produces: `main() -> int`
- Consumes: repository tracked-file list from `git ls-files -z`

- [ ] **Step 1: Write failing scanner unit tests**

Create `tests/test_canonical_env_vocabulary.py` with tests that construct retired identifiers from fragments so the test source itself does not contain a banned identifier as a contiguous token:

```python
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.check_canonical_env_vocabulary import scan_repository, scan_text

ROOT = Path(__file__).parents[1]


class CanonicalEnvVocabularyTests(unittest.TestCase):
    def test_scan_text_flags_retired_identifier_at_identifier_boundaries(self) -> None:
        retired = "CHAINNODE_API_" + "KEY"
        self.assertEqual(scan_text(f"{retired}=secret\n"), (retired,))

    def test_scan_text_does_not_flag_plural_canonical_identifier(self) -> None:
        retired = "CHAINNODE_API_" + "KEY"
        canonical = retired + "S"
        self.assertEqual(scan_text(f"{canonical}=secret\n"), ())

    def test_scan_text_flags_previous_retry_attribute(self) -> None:
        retired = "provider_retry_max_consecutive_" + "failures"
        self.assertEqual(scan_text(f"value = settings.{retired}\n"), (retired,))

    def test_scan_repository_reports_path_line_and_identifier(self) -> None:
        retired = "XKIRO_TEXT_" + "MODEL"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text(f"ok\n{retired}=old\n", encoding="utf-8")
            with patch(
                "scripts.check_canonical_env_vocabulary.tracked_files",
                return_value=(target,),
            ):
                self.assertEqual(
                    scan_repository(root),
                    (f"sample.txt:2:{retired}",),
                )

    def test_repository_has_no_retired_provider_or_env_identifiers(self) -> None:
        self.assertEqual(scan_repository(ROOT), ())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python -m unittest tests.test_canonical_env_vocabulary -v
```

Expected: import failure because `scripts.check_canonical_env_vocabulary` does not exist yet.

- [ ] **Step 3: Implement the minimal scanner**

Create `scripts/check_canonical_env_vocabulary.py` with the following implementation shape. Keep retired identifiers fragmented in source so the scanner can scan its own file without self-triggering:

```python
from __future__ import annotations

from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).parents[1]

_RETIRED_IDENTIFIER_PARTS: tuple[tuple[str, ...], ...] = (
    ("CHAINNODE_API_", "KEY"),
    ("CHAINNODE_TEXT_", "MODEL"),
    ("CHAINNODE_VISION_", "MODEL"),
    ("XKIRO_API_", "KEY"),
    ("XKIRO_TEXT_", "MODEL"),
    ("XKIRO_VISION_", "MODEL"),
    ("PROVIDER_RETRY_MAX_CONSECUTIVE_", "FAILURES"),
    ("PROVIDER_RETRY_MAX_", "FAILURES_PER_PROVIDER"),
    ("PROVIDER_RETRY_MAX_", "FAILURES_PER_REQUEST"),
    ("chainnode_api_", "key"),
    ("chainnode_text_", "model"),
    ("chainnode_vision_", "model"),
    ("xkiro_api_", "key"),
    ("xkiro_text_", "model"),
    ("xkiro_vision_", "model"),
    ("provider_retry_max_consecutive_", "failures"),
    ("provider_retry_max_", "failures_per_provider"),
    ("provider_retry_max_", "failures_per_request"),
)


def retired_identifiers() -> tuple[str, ...]:
    return tuple("".join(parts) for parts in _RETIRED_IDENTIFIER_PARTS)


def _pattern(identifier: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])"
    )


def scan_text(text: str) -> tuple[str, ...]:
    hits = [
        identifier
        for identifier in retired_identifiers()
        if _pattern(identifier).search(text)
    ]
    return tuple(hits)


def tracked_files(root: Path) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    names = tuple(name for name in completed.stdout.split(b"\0") if name)
    return tuple(root / name.decode("utf-8") for name in names)


def scan_repository(root: Path = ROOT) -> tuple[str, ...]:
    patterns = tuple((identifier, _pattern(identifier)) for identifier in retired_identifiers())
    hits: list[str] = []

    for path in tracked_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        relative = path.relative_to(root)
        for line_number, line in enumerate(text.splitlines(), start=1):
            for identifier, pattern in patterns:
                if pattern.search(line):
                    hits.append(f"{relative}:{line_number}:{identifier}")

    return tuple(hits)


def main() -> int:
    hits = scan_repository(ROOT)
    if not hits:
        print("Canonical environment vocabulary scan passed.")
        return 0

    print("Retired environment/provider identifiers found:")
    for hit in hits:
        print(hit)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python -m unittest tests.test_canonical_env_vocabulary -v
```

Expected: all tests pass, including the repository-level zero-hit assertion.

If the repository-level test fails, do not continue to Task 2. Record the exact emitted paths and identifiers, add those exact files to this plan, and resolve them according to the design contract before resuming.

- [ ] **Step 5: Run the scanner directly**

Run:

```bash
python scripts/check_canonical_env_vocabulary.py
```

Expected output:

```text
Canonical environment vocabulary scan passed.
```

Expected exit code: `0`.

- [ ] **Step 6: Commit the scanner and tests**

```bash
git add scripts/check_canonical_env_vocabulary.py tests/test_canonical_env_vocabulary.py
git commit -m "test: enforce canonical env vocabulary"
```

---

### Task 2: Wire the vocabulary gate into GitHub Actions

**Files:**
- Modify: `.github/workflows/audit.yml`
- Test: `tests/test_canonical_env_vocabulary.py`

**Interfaces:**
- Consumes: `python scripts/check_canonical_env_vocabulary.py`
- Produces: explicit CI step named `Check canonical environment vocabulary`

- [ ] **Step 1: Add the explicit CI step**

In `.github/workflows/audit.yml`, add this step immediately after `Check lint and dependency compatibility` and before targeted test groups:

```yaml
      - name: Check canonical environment vocabulary
        run: python scripts/check_canonical_env_vocabulary.py
```

Do not change any other workflow commands in this task. In particular, preserve both commands currently under `Offline regression and integration tests`.

- [ ] **Step 2: Validate the workflow diff is scope-limited**

Run:

```bash
git diff -- .github/workflows/audit.yml
```

Expected: exactly one new two-line workflow step plus surrounding YAML context; no removed test command, matrix change, action pin change, or timeout change.

- [ ] **Step 3: Run the new gate and focused tests again**

```bash
python scripts/check_canonical_env_vocabulary.py
python -m unittest tests.test_canonical_env_vocabulary -v
```

Expected: both commands exit `0`.

- [ ] **Step 4: Commit the workflow integration**

```bash
git add .github/workflows/audit.yml
git commit -m "ci: gate retired env vocabulary"
```

---

### Task 3: Run the complete local acceptance gate on the final tree

**Files:**
- Verify only; no planned edits.

**Interfaces:**
- Consumes: canonical-vocabulary scanner and existing project test/build commands.
- Produces: fresh local evidence for acceptance criteria 5-7 before push.

- [ ] **Step 1: Confirm the worktree is clean before verification**

```bash
git status --short
```

Expected: no output.

- [ ] **Step 2: Run static and dependency compatibility checks**

```bash
python -m ruff check .
python -m pip check
python -m compileall -q app tests scripts
```

Expected: all commands exit `0`.

- [ ] **Step 3: Run the explicit whole-tree canonical-vocabulary gate**

```bash
python scripts/check_canonical_env_vocabulary.py
```

Expected: exit `0` with `Canonical environment vocabulary scan passed.`

- [ ] **Step 4: Run the project launcher and full unittest discovery**

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: both commands exit `0`. Preserve this duplicate execution for branch parity with the current workflow; optimization is out of scope here.

- [ ] **Step 5: Run dependency vulnerability audit**

```bash
python -m pip_audit --progress-spinner off
```

Expected: exit `0` and no known vulnerabilities reported.

- [ ] **Step 6: Validate SearXNG YAML**

```bash
python -c "from pathlib import Path; import yaml; path = Path('searxng/settings.example.yml'); yaml.safe_load(path.read_text(encoding='utf-8')); print(f'Validated {path}')"
```

Expected: exit `0` and print the validated file path.

- [ ] **Step 7: Validate Docker Compose with the canonical template**

```bash
cp .env.example .env
docker compose config --quiet
rm .env
```

Expected: Compose exits `0`; the temporary `.env` is removed afterward.

- [ ] **Step 8: Build the production image from the verified tree**

```bash
HEAD=$(git rev-parse --short=12 HEAD)
docker build --tag "fcai-audit:${HEAD}" .
```

Expected: Docker build exits `0`.

- [ ] **Step 9: Reconfirm no verification command dirtied the tree**

```bash
git status --short
```

Expected: no output.

---

### Task 4: Push the exact final HEAD and verify GitHub Actions

**Files:**
- Verify only; no planned edits.

**Interfaces:**
- Consumes: final local HEAD and `.github/workflows/audit.yml`.
- Produces: exact-SHA CI evidence for Python 3.11 and 3.12.

- [ ] **Step 1: Capture the exact final HEAD**

```bash
HEAD=$(git rev-parse HEAD)
printf '%s\n' "$HEAD"
```

Keep this SHA as the only accepted CI target for the remaining steps.

- [ ] **Step 2: Push the feature branch without force**

```bash
git push -u origin feat/provider-env-cleanup
```

Expected: push succeeds normally. If rejected because the remote moved, stop and investigate; do not force-push.

- [ ] **Step 3: Resolve the workflow run for exactly that SHA**

```bash
RUN_ID=$(gh run list \
  --workflow audit.yml \
  --commit "$HEAD" \
  --limit 1 \
  --json databaseId,headSha \
  --jq 'select(.[0].headSha == env.HEAD) | .[0].databaseId')
test -n "$RUN_ID"
printf '%s\n' "$RUN_ID"
```

If the execution environment uses the GitHub connector instead of `gh`, fetch the `Audit checks` run by this exact SHA and apply the same equality requirement before accepting it.

- [ ] **Step 4: Wait for the exact run and require success**

```bash
gh run watch "$RUN_ID" --exit-status
```

Expected: exit `0`.

- [ ] **Step 5: Verify matrix jobs and required steps**

```bash
gh run view "$RUN_ID" --json headSha,conclusion,jobs
```

Acceptance requirements:

- `headSha` equals `$HEAD` exactly.
- Overall conclusion is `success`.
- Python 3.11 job concludes `success`.
- Python 3.12 job concludes `success`.
- `Check canonical environment vocabulary` concludes `success`.
- Python 3.12 also reports successful Docker Compose validation and production image build.

Do not accept an older green run or a run for a parent commit.

---

### Task 5: Perform the final Standards + Spec review

**Files:**
- Review: all changes in `main...feat/provider-env-cleanup`
- Spec: `docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md`
- Plan evidence: `docs/superpowers/plans/2026-09-17-provider-env-cleanup-finalization.md`

**Interfaces:**
- Consumes: exact green final HEAD from Task 4.
- Produces: final review with zero blocking Standards findings and zero blocking Spec findings.

- [ ] **Step 1: Reconfirm the fixed point has not moved**

```bash
TESTED_HEAD="$HEAD"
CURRENT_HEAD=$(git rev-parse HEAD)
test "$CURRENT_HEAD" = "$TESTED_HEAD"
```

Expected: exit `0`.

- [ ] **Step 2: Run the code-review workflow against `main...feat/provider-env-cleanup`**

Use the repository's `code-review` skill with:

```text
Base: main
Head: feat/provider-env-cleanup
Required exact head: $TESTED_HEAD
Axes: Standards + Spec
Spec: docs/superpowers/specs/2026-09-17-canonical-env-cutover-design.md
```

Review the complete branch delta, not only the final two commits.

- [ ] **Step 3: Require zero blocking findings**

Acceptance condition:

```text
Standards blocking findings: 0
Spec blocking findings: 0
```

If either count is non-zero, stop. Fix findings through a new reviewed task, rerun Task 3 completely, obtain a new exact HEAD, rerun Task 4, then rerun this review. Never reuse prior CI evidence after changing the tree.

- [ ] **Step 4: Re-run the explicit vocabulary gate after review**

```bash
python scripts/check_canonical_env_vocabulary.py
```

Expected: exit `0`.

This is the final local check that directly proves acceptance criterion 5 on the same reviewed tree.

---

### Task 6: Integration handoff without automatic merge

**Files:**
- No code changes.

**Interfaces:**
- Consumes: exact tested/reviewed HEAD with all acceptance criteria satisfied.
- Produces: human integration choice; branch remains intact unless the human explicitly chooses an integration action.

- [ ] **Step 1: Present the branch completion choices**

Present exactly:

```text
Implementation complete. What would you like to do?

1. Merge back to main locally
2. Push and create a Pull Request
3. Keep the branch as-is (I'll handle it later)

Which option?
```

Recommended operational path for this branch is option 2 so the unusually broad branch delta retains a review trail, but the integration choice belongs to the human partner.

- [ ] **Step 2: If option 2 is chosen, create the PR against `main`**

Before creating the PR, confirm:

```bash
test "$(git rev-parse HEAD)" = "$TESTED_HEAD"
git status --short
```

Expected: exact tested HEAD and clean worktree.

Then push if needed and create the PR using the repository's PR template/conventions. The PR body must include:

- canonical environment cutover scope;
- explicit whole-tree vocabulary gate added by this finalization;
- exact tested HEAD SHA;
- local acceptance commands completed;
- GitHub Actions run ID for exact HEAD;
- final Standards/Spec review result;
- note that duplicate full-suite CI execution is intentionally deferred as a separate non-blocking cleanup.

Do not merge the PR automatically.

---

## Self-Review Checklist

- Spec coverage: Tasks 1-5 directly cover whole-tree criterion 5, local verification criterion 6, Docker criterion 7, exact-HEAD CI criterion 8, and final review criterion 9. Criteria 1-4 remain protected by the existing branch implementation and the new zero-hit repository regression.
- Placeholder scan: no implementation placeholder is permitted. Unexpected whole-tree hits are explicitly a stop condition requiring the plan to be amended with exact paths before edits.
- Type consistency: scanner interfaces are defined once and consumed consistently by tests and CLI.
- Scope control: no provider behavior changes, no compatibility shim, no xKiro model hard-coding, no retry/timeout/order semantic changes, and no duplicate-test CI cleanup are included.
- Evidence rule: any code change after Task 3 invalidates Tasks 3-5 evidence and requires a full rerun on a new HEAD.
