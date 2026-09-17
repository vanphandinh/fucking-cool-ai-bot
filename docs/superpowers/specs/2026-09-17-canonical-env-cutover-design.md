# Canonical Environment Cutover Design

**Status:** Implemented design contract

## Goal

The current repository tree exposes one canonical environment system. Runtime, helper scripts, tests and retained documentation use only current provider pools, current retry names and current provider vocabulary. Git history remains the archive for superseded contracts.

## Canonical environment contract

Provider configuration:

```env
CHAINNODE_API_KEYS=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0

XKIRO_API_KEYS=
XKIRO_BASE_URL=https://api.xkiro.com/v1
XKIRO_TEXT_MODELS=
XKIRO_VISION_MODELS=
XKIRO_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Chainnode ships qualified text and vision model defaults in the canonical deployment template so a configured Chainnode credential pool is operational without an additional model-selection step. xKiro model pools intentionally remain blank until current candidates pass live qualification; the runtime does not hard-code xKiro model IDs.

Retry/recovery configuration:

```env
PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5
PROVIDER_RECOVERY_MAX_HOPS_PER_REQUEST=5
```

Python setting attributes match this vocabulary:

- `provider_retry_max_consecutive`
- `provider_retry_max_per_provider`
- `provider_retry_max_per_request`
- `provider_recovery_max_hops_per_request`

Provider runtime is plural-only. `XKIRO_DEFAULT_BASE_URL` is the shared default endpoint constant for runtime and probe behavior.

## Strict environment synchronization

`scripts/sync_env.py` is a strict template synchronizer. `.env.example` is the complete supported key schema.

The synchronizer:

- safely parses `.env.example` and `.env`;
- rejects duplicate or malformed assignments;
- preserves existing values only for keys present in the canonical template;
- adds missing canonical keys from template defaults;
- rejects every unknown `.env` assignment with exit code `2` before rewrite;
- preserves template ordering/comments;
- avoids rewrites when content is unchanged;
- writes atomically and maintains owner-only permissions.

The synchronizer does not infer provider order, translate names, or copy values between different keys. Deployment configuration must already conform to the canonical schema before this revision is used.

## Provider order

Only currently registered providers belong in the normal provider order:

```env
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

The synchronizer does not rewrite provider names or infer replacements. Runtime registry/validation owns current provider semantics.

## Tests

Current tests cover:

- canonical provider pool fields and retry fields;
- canonical provider/retry defaults in the implemented design contract stay value-aligned with `.env.example`;
- absence of validation aliases on canonical retry settings;
- model-major × credential provider slot expansion;
- shared xKiro base URL behavior between runtime and probe;
- canonical value preservation and missing-key population by sync;
- unknown-key rejection before rewrite;
- duplicate/malformed assignment failure;
- sync idempotence and owner-only permission hardening;
- provider routing/retry behavior using exact canonical Python setting names;
- xKiro catalog-gate reporting in the main probe suite without duplicated responsibility.

Tests describe only the current system and do not retain fixtures whose sole purpose is superseded compatibility behavior.

## Documentation

README and active provider/env-sync guides describe only the canonical contract and strict-sync behavior. Retained `docs/superpowers/` material keeps durable design value only; obsolete implementation guidance is removed rather than maintained as current documentation.

## Deployment cutover

Required operator sequence:

1. Back up current production environment/secret configuration outside the repository.
2. Populate every canonical provider pool, retry, recovery and other setting required by the deployment.
3. Remove every assignment that is not present in the deployed `.env.example`.
4. Confirm provider orders contain only currently registered providers.
5. Deploy the canonical-only revision.
6. Run `python scripts/sync_env.py` and require exit `0`.
7. Run it a second time and confirm no content rewrite.
8. Rebuild/restart the bot.
9. Smoke text, vision, Chainnode-primary behavior and controlled xKiro fallback.

## Failure behavior

The system fails closed for configuration boundary violations:

- unknown `.env` assignment: sync exits `2`, no rewrite;
- malformed/duplicate assignment: sync exits `2`, no rewrite;
- invalid canonical runtime value: `Settings` validation fails;
- missing required provider models for an enabled configured provider: runtime validation fails.

No failure path copies, infers or translates values into another setting.

## Whole-tree acceptance criteria

Implementation is complete only when:

1. runtime/helper scripts contain only the canonical environment contract;
2. `.env.example` contains only current names;
3. active and retained docs contain only current environment guidance;
4. current tests describe canonical behavior without superseded fixtures;
5. a whole-tree scan finds no retired environment/provider vocabulary;
6. Ruff, dependency checks, compileall, full unittest discovery and dependency audit pass;
7. Docker Compose validation and the production image build pass;
8. GitHub Actions for the exact final branch HEAD is green on Python 3.11 and 3.12;
9. final branch review has zero blocking Standards or Spec findings.

## Non-goals

- Do not rewrite Git history.
- Do not change provider selection policy beyond the canonical environment boundary.
- Do not add a migration utility or compatibility shim.
- Do not hard-code xKiro model IDs.
- Do not alter retry limits, timeout values, provider target ordering or recovery semantics beyond the canonical setting names above.
