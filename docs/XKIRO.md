# xKiro fallback provider

xKiro is the secondary AI provider for both text and vision. Chainnode remains production primary. xKiro is activated only when an API key and an explicitly configured, live-qualified model are present for the selected route.

## Endpoint, auth, and wire contract

Runtime uses the OpenAI-compatible xKiro API:

```text
Base URL: https://api.xkiro.com/v1
POST /chat/completions
Authorization: Bearer <XKIRO_API_KEY>
```

Runtime requests explicitly send:

```json
{"stream": false}
```

The adapter does not inject provider-specific reasoning flags or a `tool_choice=none` workaround. Text and vision use separate provider slots under the stable provider key `xkiro`; the vision slot advertises `max_images=1`.

## Configuration

```env
XKIRO_API_KEY=
XKIRO_TEXT_MODEL=
XKIRO_VISION_MODEL=
XKIRO_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

Model IDs are deliberately empty in `.env.example`. The xKiro catalog changes independently of this repository, so the runtime does not keep a hard-coded free-model allowlist and does not query the catalog during startup.

If `XKIRO_API_KEY` is configured and `xkiro` is selected for text, `XKIRO_TEXT_MODEL` is required. If vision is enabled and xKiro is selected for vision, `XKIRO_VISION_MODEL` is also required. With no xKiro key, the xKiro family returns zero active slots and Chainnode continues normally. Provider factories trim surrounding whitespace from Chainnode/xKiro API keys and treat whitespace-only values as absent, so accidental spacing cannot create a bogus active provider or malformed bearer token.

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

The retained probe reads the key only from `XKIRO_API_KEY` and emits machine-readable JSONL records:

```bash
export XKIRO_API_KEY='...'
python scripts/probe_xkiro.py \
  --text-model '<candidate-text-id>' \
  --vision-model '<candidate-vision-id>' \
  --image './known-test-image.png'
```

Prefer exporting the key through the deployment secret mechanism so it is not placed in shell history. The probe never logs the Authorization header or API key.

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

Do not hard-code the selected IDs into `app/ai/xkiro.py`; set them in deployment configuration.

At the time of this code migration, no xKiro production model is claimed as qualified unless the live probe has actually been run with an operator-provided `XKIRO_API_KEY`.

## Tool and portability behavior

xKiro uses the same generic router/tool architecture as Chainnode:

- structured tool calls are parsed by the shared OpenAI-compatible adapter;
- completed tools are not re-executed during provider rotation;
- provider-local assistant metadata is not carried to another provider;
- Fresh Synthesis uses provider-neutral evidence with tools disabled after the budget closes;
- retry budgets, provider-health semantics, and question-control behavior are unchanged by this integration.

## Quota and rate limits

Free-tier availability, per-account quota, and rate limits are external xKiro behavior and can change without a repository release. Treat a 429 as an operational signal, honor `Retry-After` within the configured probe wait budget, and re-check the live catalog before changing production model IDs. Do not assume a model remains free solely because it was previously qualified.

## Rollout

1. Deploy the adapter/config/probe while Chainnode remains working.
2. Configure a usable replacement **before** retiring a legacy B.AI route. Historical deployments could rely on default B.AI text/vision models and provider orders even when those overrides were absent from `.env`, so `scripts/sync_env.py` treats a legacy B.AI API key as potentially active under those historical defaults.
3. Run `scripts/sync_env.py`. The migration understands quoted/commented provider-order values and fails without rewriting `.env` if an active legacy text route has no selected Chainnode/xKiro text replacement. When vision is enabled, it likewise requires a selected replacement vision provider with API key + vision model; explicitly set `VISION_ENABLED=0` if vision is intentionally being removed.
4. Live-qualify current xKiro text and vision candidates.
5. Set `XKIRO_API_KEY`, `XKIRO_TEXT_MODEL`, and `XKIRO_VISION_MODEL` in deployment secrets/environment.
6. Keep:

```env
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

7. Rebuild/restart the bot.
8. Smoke normal text/vision and confirm Chainnode answers first.
9. Perform a controlled Chainnode failure and confirm xKiro completes the fallback request without duplicate tool execution.
10. Restore Chainnode immediately and monitor provider/fallback metrics.

Quoted-empty, whitespace-only, or commented-empty provider credentials/models are not accepted as migration replacements. B.AI credentials are never copied into xKiro.

## Rollback

The operational rollback is Chainnode-only:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

This disables xKiro fallback without changing the qualified Chainnode models. The xKiro credential/model values may remain configured but unused for a fast re-enable.

## Credential safety

- never commit `XKIRO_API_KEY`;
- never copy a credential from a retired/different provider into `XKIRO_API_KEY`;
- never print the key or Authorization header in probe output or logs;
- surrounding whitespace is stripped at provider construction and whitespace-only keys are treated as absent;
- keep the deployment `.env` owner-only as enforced by `scripts/sync_env.py`.
