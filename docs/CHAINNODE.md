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
```

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

## Transport retry policy

The OpenAI-compatible adapter performs exactly one HTTP attempt and classifies
transport failures. The router owns the bounded same-provider retry policy:

| Failure | Same-provider retry |
|---|---|
| `ConnectError` | once |
| `ConnectTimeout` | once |
| `ReadTimeout` | once only when no healthy ordered fallback remains |
| `WriteTimeout` | never |
| `PoolTimeout` | never |
| HTTP `401/403/429/4xx/5xx` | no new retry; existing policy unchanged |

A provider name can consume at most **one same-provider retry per end-user
request**. The budget is request-scoped, so a ConnectTimeout retry used before a
tool call prevents a second retry after a later ReadTimeout in the same request.

With `TEXT_PROVIDER_ORDER=chainnode,bai`, a Chainnode ReadTimeout normally falls
back to healthy B.AI immediately rather than adding another Chainnode read wait.
ConnectError/ConnectTimeout still get their one local retry first. A ReadTimeout
on the final healthy provider can use the one retry if that provider has not
already consumed it.

Retry wraps the exact failing model HTTP continuation. Completed tool execution,
portable messages, successful tool outputs and the request-wide tool budget are
not reset or rerun. Provider health records one failure only after the bounded
local retry is unavailable or exhausted; a successful retry records no failure.

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

For a retry-policy rollout, keep current production timeout values unchanged and
observe these log patterns separately: ConnectTimeout -> retry -> success,
ConnectTimeout -> retry -> failure -> fallback, and final-provider ReadTimeout
-> retry -> success/failure.

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
