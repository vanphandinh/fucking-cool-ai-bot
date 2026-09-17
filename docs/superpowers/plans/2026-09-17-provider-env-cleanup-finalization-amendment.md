# Provider Env Cleanup Finalization — Execution Amendment

**Parent plan:** `docs/superpowers/plans/2026-09-17-provider-env-cleanup-finalization.md`

**Trigger:** Task 1 repository-level vocabulary regression failed on exact HEAD `bbb872859ac2686e1bd29e5b18f5b046da9e335d`. Per the parent plan stop condition, execution pauses before CI wiring until every emitted hit is classified and resolved.

## Exact scanner findings

The first whole-tree regression reported exactly seven tracked-file hits:

1. `scripts/probe_chainnode.py:517` — retired singular Chainnode credential environment identifier.
2. `scripts/probe_chainnode.py:519` — the same retired identifier in the probe error message.
3. `scripts/probe_chainnode_vision.py:534` — retired singular Chainnode credential environment identifier.
4. `scripts/probe_chainnode_vision.py:536` — the same retired identifier in the probe error message.
5. `tests/test_crawl4ai_compose.py:32` — negative assertion containing a retired xKiro credential identifier as a literal.
6. `tests/test_provider_target_security.py:86` — negative assertion containing a retired xKiro text-model identifier as a literal.
7. `tests/test_provider_target_security.py:107` — negative assertion containing a retired Chainnode text-model identifier as a literal.

## Root-cause classification

- The four Chainnode probe hits were real legacy helper-script behavior. Both retained Chainnode probes still read one singular credential environment variable even though the canonical contract is plural-only.
- The three test hits were reference artifacts rather than compatibility behavior. They asserted that legacy names were absent, but writing those names literally violated the stronger whole-tree criterion.

## Chainnode remediation contract

Both Chainnode probes remain single-credential qualification tools. They read `CHAINNODE_API_KEYS`, split on commas, trim whitespace, discard blank entries, preserve case/order, and use the first configured credential. This preserves the existing one-credential probe behavior while aligning the environment boundary with the canonical pool contract and its left-to-right priority.

Each probe exposes:

```python
def chainnode_probe_credential() -> str:
    ...
```

The function returns the first non-blank canonical pool entry, or `""` when the pool is empty. `run()` calls this helper and its missing-credential error names only the canonical plural environment field.

The three negative tests keep the same semantic assertions but construct retired identifiers from adjacent string fragments so no tracked file contains those retired tokens contiguously.

## Final-review blocker: retired provider vocabulary coverage

The final review found that the first scanner implementation did not cover the retired B.AI provider forms previously protected by the repository's deleted provider-reference gate. Regression coverage was added for the standalone provider word plus active module, builder, environment-prefix, and API-host forms. The scanner now enforces those forms through one provider regex shared by `scan_text()` and `scan_repository()`.

## Final-review blocker: xKiro probe credential contract

The final review also found that `scripts/probe_xkiro.py` still reads a dedicated probe-only credential environment key instead of the canonical provider pool. This is real legacy helper-script behavior because the design requires helper scripts to use current provider pools only.

The xKiro qualification probe will align with the canonical pool contract exactly like the Chainnode probes:

```python
def xkiro_probe_credential() -> str:
    ...
```

`xkiro_probe_credential()` reads `XKIRO_API_KEYS`, splits on commas, trims whitespace, discards blank entries, preserves case/order, and returns the first configured credential or `""`. `run_probe()` uses this helper and reports `XKIRO_API_KEYS is required` when the pool is empty.

The retired probe-only key is added to the whole-tree scanner as fragmented source parts so it cannot be reintroduced later. Tests must no longer publish or depend on the dedicated probe-only key.

## TDD sequence for xKiro blocker

1. Add a scanner unit test that constructs the retired probe-only key from fragments and requires `scan_text()` to flag it.
2. Change xKiro probe contract tests to require the first non-blank `XKIRO_API_KEYS` entry and the canonical missing-pool error.
3. Verify RED on the exact test-only HEAD: scanner coverage fails because the retired key is not yet registered, and the probe helper test fails because `xkiro_probe_credential()` does not yet exist.
4. Add the retired key to the scanner through fragmented parts and implement `xkiro_probe_credential()` plus canonical error text in `scripts/probe_xkiro.py`.
5. Verify GREEN on the exact resulting HEAD: explicit whole-tree gate, full unittest suite, Ruff, dependency checks, compileall, dependency audit, Compose validation, and production image build all pass on the required matrix.
6. Re-run the complete Standards + Spec review on the new exact HEAD. Any further blocker invalidates the current CI evidence and restarts this loop.

## Post-RED deviation: current documentation still taught the retired xKiro probe key

After implementing scanner coverage and the probe change, exact-head CI on `48d2b8a3c45aefd694cb254e7e98cf539263cf75` failed at the explicit vocabulary gate. The scanner reported six documentation hits: one in top-level `README.md`, one in `docs/README.md`, and four in `docs/XKIRO.md`.

These hits are real stale current-documentation contracts, not scanner false positives: the affected guides still described a separate probe-only credential even though the accepted xKiro helper contract now consumes the canonical `XKIRO_API_KEYS` pool. The retired identifier can be described for audit purposes only as the fragmented form `XKIRO_PROBE_API_` + `KEY`, never as a contiguous tracked token.

Remediation is documentation-only: update the three current guides so the qualification probe uses `XKIRO_API_KEYS`, explicitly states first-non-blank selection for the single-credential probe, and no longer teaches or warns about a separate probe-only secret. Do not whitelist these hits. The next commit requires a fresh exact-head CI run and invalidates run `35237510881` as final evidence.

## Scope guard

This amendment changes only helper-script environment boundaries, scanner coverage, current documentation describing those boundaries, and tests that describe those boundaries. It does not alter runtime provider selection, model IDs, retry limits, timeout values, provider ordering, recovery semantics, or the intentionally deferred duplicate full-suite CI cleanup.
