# Chainnode primary text and vision provider

Chainnode is the production **primary** AI provider for both text and vision. xKiro is the secondary fallback. The router keeps separate text and vision target pools so each route can use different qualified models while sharing the same provider family.

## Qualified production split

```text
text   = cl/cline-free/deepseek-v4.1-flash
vision = cl/cline-free/muse-spark-1.3-contributor
```

The split is intentional because upstream Cline limits are model-specific. DeepSeek V4.1 Flash remains reserved for text while Muse Spark 1.3 Contributor is the vision primary.

The vision compatibility probe was run live on 2026-09-15 through the same Chainnode OpenAI-compatible gateway used by the bot. Muse passed non-stream vision with the deterministic hidden code `47-GREEN-CIRCLE`, streaming diagnostics, structured vision tool calling, and tool continuation. DeepSeek V4.1 Flash also passed the vision probe as a regression candidate but is not the production vision primary.

Model IDs and gateway behavior are external dependencies. Re-run the retained probes after gateway upgrades or model-ID changes.

## Configuration

Canonical current deployment fields use plural model pools:

```env
CHAINNODE_API_KEY=
CHAINNODE_BASE_URL=https://dn.chainno.de/v1
CHAINNODE_TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash
CHAINNODE_VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
CHAINNODE_REQUEST_TIMEOUT_SEC=60.0

XKIRO_API_KEYS=
XKIRO_TEXT_MODELS=
XKIRO_VISION_MODELS=
XKIRO_REQUEST_TIMEOUT_SEC=60.0

TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

`CHAINNODE_TEXT_MODELS` must contain at least one model when `chainnode` is selected for text and a Chainnode key is configured. `CHAINNODE_VISION_MODELS` must contain at least one model when vision is enabled, Chainnode is selected for vision, and a Chainnode key is configured.

Legacy scalar `CHAINNODE_TEXT_MODEL` / `CHAINNODE_VISION_MODEL` fields remain backward-compatible only when the corresponding plural field is absent. Fresh `.env.example` deployments already contain the plural fields, so current operators should configure the plural fields. The same canonical-plural rule applies to xKiro credential/model pools; see [`XKIRO.md`](XKIRO.md).

`CHAINNODE_REQUEST_TIMEOUT_SEC` is the per-attempt read timeout. Shared transport still uses `connect=8s`, `write=20s`, and `pool=5s`; `QUESTION_TIMEOUT_SEC` remains the outer question deadline on the legacy question path.

The API key must come from environment configuration. Do not commit it, log it, put it in documentation examples, or pass it as a command-line argument.

## Routing and fallback

Production ordering is:

```text
text:   Chainnode -> xKiro
vision: Chainnode -> xKiro
```

Chainnode model targets are ordered by the configured model pool. xKiro uses deterministic model-major × credential ordering. xKiro is only active when its canonical credential pool and the selected route model pool are configured. The runtime does not dynamically discover models.

The bounded retry/recovery scheduler remains generic:

- `ConnectError` / `ConnectTimeout`: bounded same-target retry, then rotation when appropriate;
- `ReadTimeout`: prefer another currently eligible provider when available;
- Chainnode `429`: cool down the failed model scope and rotate to another eligible model target;
- xKiro resource errors follow the credential/model/entitlement scopes documented in `XKIRO.md`;
- request/provider transport and recovery-hop budgets remain bounded;
- completed tool execution and portable evidence are not repeated when the target changes;
- provider-local reasoning/tool metadata is not carried across target rotation.

Fresh synthesis runs with the tool budget closed. It sends no tool schema, does not replay structured provider-local tool history, and does not execute new tool calls after the budget closes.

## Wire contract

Both Chainnode route pools reuse the shared OpenAI-compatible adapter and explicitly send:

```json
{"stream": false}
```

Vision messages keep the existing OpenAI-compatible `image_url` data-URL shape. Both Chainnode and xKiro vision targets currently advertise `max_images=1`; this migration does not increase the image limit.

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

1. verify normal text requests are answered by the first healthy Chainnode text target;
2. verify a one-image request is answered by the first healthy Chainnode vision target;
3. verify tool calls and continuation work without duplicate tool execution;
4. perform a controlled target/provider fallback smoke;
5. restore the primary target immediately and confirm normal traffic returns to it;
6. observe target/model/credential rotation metrics plus bounded retry behavior.

xKiro must be live-qualified independently before relying on it for fallback traffic; see [`XKIRO.md`](XKIRO.md).

## Rollback

Operational rollback after this migration is **Chainnode-only**:

```env
TEXT_PROVIDER_ORDER=chainnode
VISION_PROVIDER_ORDER=chainnode
```

This disables xKiro fallback without changing the qualified Chainnode primary split. A source rollback is only needed if the integration itself causes a runtime regression.
