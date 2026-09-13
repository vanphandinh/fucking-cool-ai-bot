# Chainnode text provider

This repository supports Chainnode as an optional OpenAI-compatible text
provider. B.AI remains the default route unless deployment explicitly opts in.
Chainnode is text-only in this integration; vision continues to use the existing
vision provider path.

## Qualified model

The production qualification completed on 2026-09-11 selected:

```text
cl/deepseek/deepseek-v4-flash
```

It passed the production hard gate with 97.5% initial bot routing, 96.1%
end-to-end tool-cycle completion, 95.7% overall production reliability, 100%
plain/tool/no-tool/long-context compatibility, zero malformed production
records, and zero internal tool-markup leaks.

Qualification was performed against the Router9/9Router gateway behavior in
release 0.5.75. Re-run the compatibility probe after gateway upgrades, provider
routing changes, or model-ID changes before relying on previous results.

## Configuration

The integration reads these environment variables:

```env
CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODEL=
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0
TEXT_PROVIDER_ORDER=bai
PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5
```

The three `PROVIDER_RETRY_MAX_*` names above are canonical. Legacy verbose
`*_FAILURES*` names remain accepted as compatibility aliases. `sync_env.py`
migrates legacy values to canonical keys and canonical values win if both forms
exist.

`CHAINNODE_REQUEST_TIMEOUT_SEC` controls the Chainnode per-HTTP-attempt read timeout.
The shared OpenAI-compatible transport uses `connect=8s`, `write=20s`, and
`pool=5s`. `QUESTION_TIMEOUT_SEC` remains the outer hard deadline for the whole
end-user question, including tools, retry and fallback. This lets slow
non-streaming model generation wait longer for data without spending the same
60 seconds establishing a dead connection.

`scripts/sync_env.py` preserves existing values. If an upgraded deployment
already has `CHAINNODE_REQUEST_TIMEOUT_SEC=30.0`, it stays `30.0` until changed
explicitly. Do not change production timeout values as part of a retry-policy
deploy.

The API key must come from the environment. Do not commit it, log it, put it in
GitHub Actions, or pass it on the command line.

The repository default remains B.AI-only. To canary the qualified Chainnode
model with B.AI fallback on the VPS:

```env
CHAINNODE_TEXT_MODEL=cl/deepseek/deepseek-v4-flash
TEXT_PROVIDER_ORDER=chainnode,bai
```

Keep the existing vision route unchanged. Chainnode does not advertise vision
support in this provider slot.

## Bounded cyclic transport retry policy

The OpenAI-compatible adapter performs exactly one HTTP attempt and classifies
transport failures. The router owns request-scoped bounded cyclic scheduling:

| Failure | Policy |
|---|---|
| `ConnectError` | retry same provider while eligible, then rotate |
| `ConnectTimeout` | retry same provider while eligible, then rotate |
| `ReadTimeout` | prefer another currently eligible provider; retry same provider only when no eligible alternative exists |
| `WriteTimeout` | no new cyclic revisit |
| `PoolTimeout` | no new cyclic revisit |
| HTTP `401/403/429/4xx/5xx` | no new cyclic behavior; existing health/fallback policy remains |

Default request-scoped bounds are:

```text
consecutive transport failures per provider = 2
cumulative transport failures per provider = 3
cumulative transport failures per request = 5
```

A valid `provider.chat()` response, including a tool-call response, resets only
that provider's consecutive counter. Provider cumulative and request cumulative
counters never reset inside the same end-user request. Each provider also gets
one monotonic request-scoped same-provider retry token; once consumed, a later
chat success does not refund it.

With `TEXT_PROVIDER_ORDER=chainnode,bai`, the router can recover through a full
wrap instead of the old one-way suffix traversal:

```text
chainnode ReadTimeout
-> bai ReadTimeout
-> chainnode success
```

Repeated ReadTimeouts remain bounded:

```text
chainnode #1 fail
-> bai #1 fail
-> chainnode #2 fail/exhaust
-> bai #2 fail/exhaust
-> AllProvidersFailed
```

ConnectError/ConnectTimeout keep same-provider-first behavior, for example:

```text
chainnode #1 ConnectTimeout
-> chainnode #2 immediate retry
-> rotate only if the provider is then exhausted/blocked
```

When rotation is required, the next scheduler selection excludes the provider
that just failed for that transition. This prevents a one-provider ring from
immediately selecting the same provider again and calling that a cyclic wrap.
A later wrap remains possible after another provider was genuinely selected.

Retry on the same provider wraps the exact failing model HTTP continuation.
Completed tool execution, portable messages, successful tool outputs and the
request-wide tool budget are not reset or rerun. Rotation/wrap also preserves
portable evidence, so a provider revisited later receives completed tool results
without causing the tool executor to run again.

ReadTimeout alternative eligibility is recomputed at failure time across the
full capability-filtered ring using current shared health and current
request-scoped budgets; the router does not reuse a stale suffix snapshot.

Shared `ProviderHealth` is separate from request retry state. Retryable transport
errors remain pending until the logical streak resolves. Recovery clears the
pending error without incrementing shared health; exhaustion or unresolved
fallback flushes one logical health failure exactly once. Shared health carries
a causal generation: success and immediate health errors advance it; a pending
transport streak captures its starting generation and is only deferred-applied
if that generation is still current. Deferred apply itself does not advance the
generation, so two independent unresolved concurrent failures can both count.
This prevents an older pending ReadTimeout from overwriting a newer success or a
newer 429 cooldown/`Retry-After` value.

Fallback metrics count provider transitions, not attempts. A same-provider retry
adds no fallback entry; `chainnode -> bai -> chainnode` records both real
transitions.

## Wire contract

Chainnode uses the shared OpenAI-compatible provider implementation. Production
requests explicitly send:

```json
{"stream": false}
```

This is intentional. Router9 can otherwise default an omitted `stream` field to
SSE behavior. Runtime parsing also fails closed on malformed structured tool
calls and internal DSML/tool markup.

## Compatibility probe

`scripts/probe_chainnode.py` is intentionally retained as an operational smoke
test. It checks model-catalog presence, non-stream chat completions, streaming
as a diagnostic, no-tool discipline, structured tool calling, and tool
continuation. It does not contain or print the API key.

Run it from the Linux VPS repository root:

```bash
export CHAINNODE_API_KEY='YOUR_KEY'

docker compose build bot

docker compose run --rm -T \
  -e CHAINNODE_API_KEY \
  -v "$(pwd)/scripts:/app/scripts:ro" \
  bot \
  python /app/scripts/probe_chainnode.py \
    --models cl/deepseek/deepseek-v4-flash
```

If deployment uses a non-default Router9 endpoint, also export and pass
`CHAINNODE_BASE_URL`:

```bash
export CHAINNODE_BASE_URL='https://your-router9-endpoint/v1'
```

Then add `-e CHAINNODE_BASE_URL` to the `docker compose run` command.

## Deployment smoke test

After enabling Chainnode, verify at least these behaviors through the real bot:

1. A simple arithmetic question answers without a tool.
2. A request for current Bitcoin price selects `web_search`.
3. A direct `https://example.com/` request selects `fetch_url`.
4. An image-reference request selects `image_search`.
5. Observe natural `ConnectTimeout -> same-provider retry -> success/fallback`.
6. Observe natural `ReadTimeout -> eligible alternative` and, if it occurs, `chainnode -> bai -> chainnode` wrap.
7. Verify completed tool evidence survives rotation/wrap without a duplicate tool execution.
8. Verify bounded all-provider failure terminates rather than spinning.
9. Verify a newer 429 `Retry-After` is not replaced by stale deferred transport health.

For retry-policy rollout, keep current production timeout values unchanged.

Watch `image_search` most closely because it was the weakest routing scenario in
the final qualification, although the model still cleared the production gate.

## Rollback

Chainnode is optional. To return to the previous production route, restore:

```env
TEXT_PROVIDER_ORDER=bai
```

To rollback only the retry implementation, revert the retry PR or deploy the
previous application commit. No timeout change is required.

## Benchmark artifacts

The large benchmark/ranking framework used during provider selection is not kept
in the production repository. It was qualification tooling rather than runtime
code. If a future model-selection exercise needs statistical ranking again, do
that work in a dedicated feature branch and keep only the resulting operational
probe and durable runtime regressions after qualification.
