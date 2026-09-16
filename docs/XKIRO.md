# xKiro fallback provider

xKiro is the secondary AI provider for both text and vision. Chainnode remains production primary. xKiro is activated only when the canonical credential/model pools contain usable values for the selected route.

## Endpoint, auth, and wire contract

Runtime uses the OpenAI-compatible xKiro API:

```text
Base URL: https://api.xkiro.com/v1
POST /chat/completions
Authorization: Bearer <one credential from XKIRO_API_KEYS>
```

Runtime requests explicitly send:

```json
{"stream": false}
```

The adapter does not inject provider-specific reasoning flags or a `tool_choice=none` workaround. Text and vision use concrete targets under the stable provider family `xkiro`; each target has a secret-free model/credential alias and the vision targets advertise `max_images=1`.

## Configuration

Canonical current deployment fields are the plural pool fields:

```env
XKIRO_API_KEYS=key1,key2
XKIRO_TEXT_MODELS=<qualified-text-model-a>,<qualified-text-model-b>
XKIRO_VISION_MODELS=<qualified-vision-model>
XKIRO_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Model pools are deliberately empty in `.env.example`. The xKiro catalog changes independently of this repository, so the runtime does not keep a hard-coded free-model allowlist and does not query the catalog during startup.

The legacy scalar fields `XKIRO_API_KEY`, `XKIRO_TEXT_MODEL`, and `XKIRO_VISION_MODEL` remain backward-compatible migration inputs only when the corresponding plural field is absent. An explicitly present blank plural field intentionally does **not** fall back to the scalar field. Because a fresh `.env` synced from `.env.example` contains the plural fields, new/current deployments must configure `XKIRO_API_KEYS`, `XKIRO_TEXT_MODELS`, and `XKIRO_VISION_MODELS` rather than relying on scalar examples.

If `XKIRO_API_KEYS` contains at least one key and `xkiro` is selected for text, `XKIRO_TEXT_MODELS` must contain at least one model. If vision is enabled and xKiro is selected for vision, `XKIRO_VISION_MODELS` must also contain at least one model. With no xKiro keys, the xKiro family returns zero active targets and Chainnode continues normally. Provider factories trim surrounding whitespace and treat whitespace-only values as absent.

Target order is deterministic and model-major × credential. For example, text models `m1,m2` and credentials `c1,c2` produce `m1/c1`, `m1/c2`, `m2/c1`, `m2/c2`.

## Live model qualification

`GET https://api.xkiro.com/v1/models` is the qualification source of truth. Do not select a model from a stale search snapshot, marketing page, model-name suffix, or an assumed `:free` convention.

A text fallback candidate must satisfy all catalog hard gates:

```text
exact model ID exists
access_tier == free
pricing.input == 0
pricing.output == 0
capabilities.tools == true
```

A vision candidate must additionally satisfy:

```text
capabilities.vision == true
```

Then the candidate must pass live compatibility probes for plain non-stream chat, structured tool calling, and tool continuation. Tool continuation uses a unique per-run result marker and passes only when the model reproduces that marker exactly after receiving the tool result; arbitrary non-empty continuation text is a failure. A vision candidate must also correctly understand a known test image and pass the vision/tool path.

## Probe

The retained single-credential qualification probe intentionally reads `XKIRO_API_KEY`; this is a probe input, not the canonical runtime pool field:

```bash
export XKIRO_API_KEY='...'
python scripts/probe_xkiro.py \
  --text-model '<candidate-text-id>' \
  --vision-model '<candidate-vision-id>' \
  --image './known-test-image.png'
```

Prefer exporting the probe key through the deployment secret mechanism so it is not placed in shell history. The probe never logs the Authorization header or API key.

Probe sequence:

```text
GET /models
  -> exact candidate exists
  -> free/pricing/tools/vision hard gates
POST /chat/completions plain text
POST /chat/completions structured tool call
POST /chat/completions tool continuation + exact per-run result-marker echo
POST /chat/completions vision
POST /chat/completions vision + tool flow
```

HTTP 429 retries are count-bounded and honor a numeric `Retry-After` only when the requested wait is within the probe wait budget. The default maximum accepted wait is 60 seconds; override it explicitly with `--max-retry-after <seconds>`. If the server requests a longer wait, qualification fails closed immediately instead of sleeping for an unbounded interval or retrying before the server-provided delay. Malformed JSON or missing response structure also fails qualification rather than being treated as success.

## Choosing fallback models

Only candidates that pass every hard gate may be considered. When multiple free compatible models pass, compare them in this order:

1. compatibility error rate across repeated runs;
2. p95 latency;
3. context window as a tie-breaker.

Do not hard-code selected IDs into `app/ai/xkiro.py`; configure the plural deployment pools instead.

At the time of this code migration, no xKiro production model is claimed as qualified unless the live probe has actually been run with an operator-provided probe credential.

## Recovery scopes

Resource failures are recovered inside xKiro before provider-family fallback when a healthy sibling target exists:

- `401` -> disable the failed credential scope;
- `402` -> disable the failed credential scope;
- `403` -> disable only the model × credential entitlement scope;
- `429` -> cool down the failed credential scope;
- `404` -> disable the failed model scope across credential siblings;
- `5xx` -> retain bounded provider-family fallback behavior.

Scoped invalidation is applied before sibling eligibility is recomputed, so already-known invalid model/credential siblings do not consume recovery hops. Recovery remains request-bounded and completed tool calls are not executed again after target rotation.

## Tool and portability behavior

xKiro uses the same generic router/tool architecture as Chainnode:

- structured tool calls are parsed by the shared OpenAI-compatible adapter;
- completed tools are not re-executed during provider/target rotation;
- provider-local assistant metadata is not carried to another target;
- Fresh Synthesis uses provider-neutral evidence with tools disabled after the budget closes;
- retry, recovery-hop, and tool budgets remain request-bounded.

## Quota and rate limits

Free-tier availability, per-account quota, and rate limits are external xKiro behavior and can change without a repository release. Treat a 429 as an operational signal, honor `Retry-After` within the configured probe wait budget, and re-check the live catalog before changing production model IDs. Do not assume a model remains free solely because it was previously qualified.

## Rollout

1. Deploy the adapter/config/probe while Chainnode remains working.
2. Configure a usable replacement **before** retiring a legacy B.AI route. Historical deployments could rely on default B.AI text/vision models and provider orders even when those overrides were absent from `.env`, so `scripts/sync_env.py` treats a legacy B.AI API key as potentially active under those historical defaults.
3. Run `scripts/sync_env.py`. The migration understands quoted/commented provider-order values and fails without rewriting `.env` if an active legacy text route has no selected Chainnode/xKiro text replacement. When vision is enabled, it likewise requires a selected replacement vision provider with credential + model; explicitly set `VISION_ENABLED=0` if vision is intentionally being removed.
4. Live-qualify current xKiro text and vision candidates using the retained probe.
5. Set canonical runtime pools `XKIRO_API_KEYS`, `XKIRO_TEXT_MODELS`, and `XKIRO_VISION_MODELS` in deployment secrets/environment. Do not leave those plural fields blank and set only the legacy scalar fields on a fresh synced `.env`.
6. Keep:

```env
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

7. Run `python scripts/sync_env.py` a second time and verify it is idempotent.
8. Rebuild/restart the bot.
9. Smoke normal text/vision and confirm Chainnode answers first.
10. Perform a controlled Chainnode failure and confirm xKiro completes the fallback request without duplicate tool execution.
11. Restore Chainnode immediately and monitor provider/fallback metrics.

Quoted-empty, whitespace-only, or commented-empty provider credentials/models are not accepted as migration replacements. B.AI credentials are never copied into xKiro.

## Rollback

The operational rollback is Chainnode-only:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

This disables xKiro fallback without changing the qualified Chainnode models. The xKiro plural credential/model values may remain configured but unused for a fast re-enable.

## Credential safety

- never commit `XKIRO_API_KEYS` values or the probe-only `XKIRO_API_KEY`;
- never copy a credential from a retired/different provider into xKiro fields;
- never print a raw key or Authorization header in probe output, target IDs, status, errors, or logs;
- credential IDs are opaque aliases such as `cred-1`, never raw keys;
- surrounding whitespace is stripped at provider construction and whitespace-only keys are treated as absent;
- keep the deployment `.env` owner-only as enforced by `scripts/sync_env.py`.
