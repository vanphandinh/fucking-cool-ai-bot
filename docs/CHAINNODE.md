# Chainnode primary text and vision provider

Chainnode is the production **primary** AI provider for both text and vision. xKiro is the secondary fallback. The router keeps separate text and vision slots so each route can use a different qualified model while sharing the same provider key.

## Qualified production split

```text
text   = cl/cline-free/deepseek-v4.1-flash
vision = cl/cline-free/muse-spark-1.3-contributor
```

The split is intentional because upstream Cline limits are model-specific. DeepSeek V4.1 Flash remains reserved for text while Muse Spark 1.3 Contributor is the vision primary.

The vision compatibility probe was run live on 2026-09-15 through the same Chainnode OpenAI-compatible gateway used by the bot. Muse passed non-stream vision with the deterministic hidden code `47-GREEN-CIRCLE`, streaming diagnostics, structured vision tool calling, and tool continuation. DeepSeek V4.1 Flash also passed the vision probe as a regression candidate but is not the production vision primary.

Model IDs and gateway behavior are external dependencies. Re-run the retained probes after gateway upgrades or model-ID changes.

## Configuration

```env
CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODEL=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODEL=cl/cline-free/muse-spark-1.3-contributor
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0

XKIRO_API_KEY=
XKIRO_TEXT_MODEL=
XKIRO_VISION_MODEL=
XKIRO_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

`CHAINNODE_TEXT_MODEL` is required when `chainnode` is selected for text and a Chainnode key is configured. `CHAINNODE_VISION_MODEL` is required when vision is enabled, Chainnode is selected for vision, and a Chainnode key is configured.

`CHAINNODE_REQUEST_TIMEOUT_SEC` is the per-attempt read timeout. Shared transport still uses `connect=8s`, `write=20s`, and `pool=5s`; `QUESTION_TIMEOUT_SEC` remains the outer question deadline on the legacy question path. Retry budgets and provider-health semantics are unchanged by the xKiro migration.

The API key must come from environment configuration. Do not commit it, log it, put it in documentation examples, or pass it as a command-line argument.

## Routing and fallback

Production ordering is:

```text
text:   Chainnode -> xKiro
vision: Chainnode -> xKiro
```

xKiro is only active when its API key and route model are configured. The runtime does not dynamically discover or rotate models.

The bounded cyclic retry scheduler remains generic:

- `ConnectError` / `ConnectTimeout`: bounded same-provider retry, then rotation when appropriate;
- `ReadTimeout`: prefer another currently eligible provider when available;
- non-retryable transport/status failures keep the existing health/fallback policy;
- request/provider retry budgets remain unchanged;
- completed tool execution and portable evidence are not repeated when the provider changes;
- provider-local reasoning/tool metadata is not carried across provider rotation.

Fresh synthesis runs with the tool budget closed. It sends no tool schema, does not replay structured provider-local tool history, and does not execute new tool calls after the budget closes.

## Wire contract

Both Chainnode slots reuse the shared OpenAI-compatible adapter and explicitly send:

```json
{"stream": false}
```

Vision messages keep the existing OpenAI-compatible `image_url` data-URL shape. Both Chainnode and xKiro vision slots currently advertise `max_images=1`; this migration does not increase the image limit.

## Compatibility probes

### Text/tool

Run the retained Chainnode probe with the production text model:

```bash
python scripts/probe_chainnode.py \
  --models cl/cline-free/deepseek-v4.1-flash
```

It checks catalog presence, non-stream chat, no-tool discipline, structured tool calling, and tool continuation. Streaming remains diagnostic.

### Vision

```bash
python scripts/probe_chainnode_vision.py \
  --models cl/cline-free/muse-spark-1.3-contributor,cl/cline-free/deepseek-v4.1-flash
```

The production Muse gate requires correct deterministic image recognition and compatible structured tool behavior.

Never pass credentials as command-line arguments. Export them in the environment or use the deployment secret mechanism.

## Production smoke

After deployment:

1. verify normal text requests are answered by Chainnode first;
2. verify a one-image request is answered by the Chainnode vision slot;
3. verify tool calls and continuation work without duplicate tool execution;
4. perform a controlled fallback smoke so a Chainnode failure transitions to xKiro;
5. restore Chainnode immediately and confirm normal traffic returns to the primary;
6. observe fallback/health metrics and bounded retry behavior.

xKiro must be live-qualified independently before relying on it for fallback traffic; see [`XKIRO.md`](XKIRO.md).

## Rollback

Operational rollback after this migration is **Chainnode-only**:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

This disables xKiro fallback without changing the qualified Chainnode primary split. A source rollback is only needed if the integration itself causes a runtime regression.
