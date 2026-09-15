# Chainnode text and vision provider

This repository supports Chainnode as an optional OpenAI-compatible provider
for both text and vision. The routes are separate provider slots, so they can
use different models and can be enabled or rolled back independently. B.AI
remains the repository default and fallback route unless deployment explicitly
opts in.

## Qualified production split

The production target for this rollout is:

```text
text   = cl/cline-free/deepseek-v4.1-flash
vision = cl/cline-free/muse-spark-1.3-contributor
```

The split is intentional. Cline currently applies limits per model, so reserving
DeepSeek V4.1 Flash for text and Muse Spark 1.3 Contributor for vision separates
load across those model quotas while B.AI remains available as fallback for both
routes.

The vision compatibility probe was run live on 2026-09-15 through the same
Chainnode OpenAI-compatible gateway used by the bot. Muse passed non-stream
vision with the exact hidden image code `47-GREEN-CIRCLE`, streaming vision,
structured vision tool calling, and tool continuation. DeepSeek V4.1 Flash also
passed the vision probe and is retained as a regression candidate, but it is not
the production vision primary so its quota stays available for text. GLM 5.3
Flash also passed the qualification probe and remains an unused candidate rather
than an active production route.

### Historical text qualification

The earlier production text qualification completed on 2026-09-11 selected
`cl/deepseek/deepseek-v4-flash`. It passed the production hard gate with 97.5%
initial bot routing, 96.1% end-to-end tool-cycle completion, 95.7% overall
production reliability, 100% plain/tool/no-tool/long-context compatibility,
zero malformed production records, and zero internal tool-markup leaks. That
qualification was performed against Router9/9Router gateway behavior in release
0.5.75.

Model IDs and gateway behavior are external dependencies. Re-run the retained
compatibility probes after gateway upgrades, provider-routing changes, or model
ID changes before relying on earlier qualification results.

## Configuration

The integration reads these environment variables:

```env
CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODEL=
CHAINNODE_VISION_MODEL=
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0
TEXT_PROVIDER_ORDER=bai
VISION_PROVIDER_ORDER=bai
PROVIDER_RETRY_MAX_CONSECUTIVE=2
PROVIDER_RETRY_MAX_PER_PROVIDER=3
PROVIDER_RETRY_MAX_PER_REQUEST=5
```

`CHAINNODE_TEXT_MODEL` is required only when `chainnode` is selected in
`TEXT_PROVIDER_ORDER`. `CHAINNODE_VISION_MODEL` is required only when vision is
enabled and `chainnode` is selected in `VISION_PROVIDER_ORDER`. The repository
defaults remain B.AI-only, so adding this runtime support does not activate
Chainnode traffic by itself.

The three `PROVIDER_RETRY_MAX_*` names above are canonical. Legacy verbose
`*_FAILURES*` names remain accepted as compatibility aliases. `sync_env.py`
migrates legacy values to canonical keys and canonical values win if both forms
exist.

`CHAINNODE_REQUEST_TIMEOUT_SEC` controls the Chainnode per-HTTP-attempt read timeout
for both slots. The shared OpenAI-compatible transport uses `connect=8s`,
`write=20s`, and `pool=5s`. `QUESTION_TIMEOUT_SEC` remains the outer hard
deadline for the whole end-user question, including tools, retry and fallback.
This lets slow non-streaming model generation wait longer for data without
spending the same 60 seconds establishing a dead connection.

`scripts/sync_env.py` preserves existing values while adding newly documented
keys. If an upgraded deployment already has an explicit timeout or provider
value, it stays unchanged until changed deliberately. Do not change production
timeout values as part of this provider/model rollout.

The API key must come from the environment. Do not commit it, log it, put it in
GitHub Actions, or pass it on the command line.

## Live target configuration

After the code has been merged, deployed, and smoke-tested with the old B.AI
vision route still active, enable the split with:

```env
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
TEXT_PROVIDER_ORDER=chainnode,bai
VISION_PROVIDER_ORDER=chainnode,bai
VISION_ENABLED=1
```

The initial Chainnode vision slot advertises one image per request. Do not raise
the image count as part of this rollout.

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

With `TEXT_PROVIDER_ORDER=chainnode,bai` or
`VISION_PROVIDER_ORDER=chainnode,bai`, the applicable capability-filtered ring
can recover through a full wrap instead of the old one-way suffix traversal:

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

Both Chainnode slots use the shared OpenAI-compatible provider implementation.
Production requests explicitly send:

```json
{"stream": false}
```

This is intentional. Router9 can otherwise default an omitted `stream` field to
SSE behavior. Runtime parsing also fails closed on malformed structured tool
calls and internal DSML/tool markup.

Vision requests preserve the existing OpenAI-compatible multimodal message
shape:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "Describe this image"},
    {
      "type": "image_url",
      "image_url": {"url": "data:image/png;base64,..."}
    }
  ]
}
```

## Compatibility probes

### Text/tool probe

`scripts/probe_chainnode.py` is intentionally retained as an operational smoke
test. It checks model-catalog presence, non-stream chat completions, streaming
as a diagnostic, no-tool discipline, structured tool calling, and tool
continuation. It does not contain or print the API key.

Read the API key without putting it in shell history:

```bash
read -s CHAINNODE_API_KEY
export CHAINNODE_API_KEY
echo
```

Before activating the new split, the DeepSeek V4.1 text target must pass this
probe through the same container/network path as production:

```bash
docker compose build bot

docker compose run --rm -T \
  -e CHAINNODE_API_KEY \
  -v "$(pwd)/scripts:/app/scripts:ro" \
  bot \
  python /app/scripts/probe_chainnode.py \
    --models cl/cline-free/deepseek-v4.1-flash
```

The required text gate is a successful catalog lookup plus compatible production
non-stream plain chat, no-tool discipline, structured tool calling, and tool
continuation for `cl/cline-free/deepseek-v4.1-flash`. Streaming remains
diagnostic only. The historical `cl/deepseek/deepseek-v4-flash` qualification
does not substitute for this gate because it is a different model ID.

### Vision probe

`scripts/probe_chainnode_vision.py` qualifies image input with a deterministic
image whose answer is not present in the text prompt. It checks the production
non-stream path and also reports streaming, structured tool call, and tool
continuation diagnostics.

Run the final vision regression gate from a networked host:

```bash
python scripts/probe_chainnode_vision.py \
  --models cl/cline-free/muse-spark-1.3-contributor,cl/cline-free/deepseek-v4.1-flash
```

The required Muse production gate is `nonstream_vision` with
`compatible=true` and exact `47-GREEN-CIRCLE`. The structured vision tool call
and continuation should also remain compatible. DeepSeek vision is checked here
only as a regression candidate; production vision still uses Muse.

If deployment uses a non-default Router9 endpoint, also export
`CHAINNODE_BASE_URL` and pass it into Docker when using the containerized probe:

```bash
export CHAINNODE_BASE_URL='https://your-router9-endpoint/v1'
```

Do not pass credentials as command-line arguments.

## Production rollout and smoke test

Deploy the merged runtime first without activating Chainnode vision. Run
`scripts/sync_env.py`, keep:

```env
VISION_PROVIDER_ORDER=bai
```

then rebuild/restart the bot and verify the existing B.AI vision path still
works. This separates runtime-regression risk from upstream provider/model risk.
Only after that no-change smoke test and both required live compatibility gates
(DeepSeek V4.1 text and Muse vision) succeed should the live target configuration
above be applied and the bot service recreated.

After enabling Chainnode, verify at least these behaviors through the real bot:

1. A simple arithmetic question answers without a tool.
2. A request for current Bitcoin price selects `web_search`.
3. A direct `https://example.com/` request selects `fetch_url`.
4. An image-reference request selects `image_search`.
5. A one-image understanding request is answered correctly through the vision route.
6. An image conversation that can invoke a tool completes without malformed tool markup or duplicate tool execution.
7. Observe natural `ConnectTimeout -> same-provider retry -> success/fallback`.
8. Observe natural `ReadTimeout -> eligible alternative` and, if it occurs, `chainnode -> bai -> chainnode` wrap.
9. Verify completed tool evidence survives rotation/wrap without a duplicate tool execution.
10. Verify bounded all-provider failure terminates rather than spinning.
11. Verify a newer 429 `Retry-After` is not replaced by stale deferred transport health.
12. If a natural Chainnode 429/5xx occurs, verify B.AI completes the affected request instead of failing the whole user request.

Keep current timeout and retry-budget values unchanged during this rollout so a
provider/model change is not mixed with latency-policy changes. Continue to
watch `image_search` because it was the weakest routing scenario in the earlier
text qualification, although the model still cleared that production gate.

## Rollback

Vision can be rolled back independently without changing the text route:

```env
VISION_PROVIDER_ORDER=bai
```

Recreate the bot service after changing the environment. This leaves DeepSeek
text on Chainnode when `TEXT_PROVIDER_ORDER=chainnode,bai` remains configured.

If image handling itself must be disabled immediately:

```env
VISION_ENABLED=0
```

If Chainnode text also needs to be rolled back:

```env
TEXT_PROVIDER_ORDER=bai
VISION_PROVIDER_ORDER=bai
```

For a broader runtime regression, restore the pre-rollout environment backup and
deploy the previously recorded production commit. To rollback only the bounded
retry implementation itself, revert that retry change or deploy the previous
application commit; no timeout change is required.

## Benchmark artifacts

The retained probes are compatibility gates, not latency or quota benchmarks.
They establish payload/tool compatibility but do not rank p50/p95 latency,
long-run timeout rate, or image-understanding quality across complex real-world
images. Future statistical ranking should stay in dedicated qualification work
rather than adding model rotation or adaptive scoring to this runtime change.
