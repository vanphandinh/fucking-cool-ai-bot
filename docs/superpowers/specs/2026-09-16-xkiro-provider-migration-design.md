# xKiro Provider Migration Design

## Goal

Keep Chainnode as the primary AI provider for both text and vision, add xKiro as the secondary/fallback provider, and remove B.AI completely from active runtime code, configuration, probes, tests, and maintained documentation.

## Target routing

Production routing after migration:

```text
text:   Chainnode -> xKiro
vision: Chainnode -> xKiro
```

The already-qualified Chainnode split remains unchanged in production:

```text
text   = cl/cline-free/deepseek-v4.1-flash
vision = cl/cline-free/muse-spark-1.3-contributor
```

Repository provider-order defaults become:

```env
TEXT_PROVIDER_ORDER=chainnode,xkiro
VISION_PROVIDER_ORDER=chainnode,xkiro
```

xKiro model IDs remain explicit deployment configuration, not a hard-coded allowlist. `GET https://api.xkiro.com/v1/models` is the qualification source of truth because the catalog changes independently of this repository. The runtime must not perform a catalog network call during startup.

## xKiro adapter

Create `app/ai/xkiro.py` as a thin provider-family adapter over the existing `OpenAICompatProvider`.

Contract:

- base URL: `https://api.xkiro.com/v1`
- auth: existing `Authorization: Bearer <key>` behavior
- endpoint: `POST chat/completions`
- `stream=false` explicitly, because the runtime parser is blocking JSON rather than SSE
- separate text and vision slots under provider name `xkiro`
- initial vision capability advertises `max_images=1`
- no B.AI-specific `tool_choice=none` workaround
- no runtime free-model allowlist and no model-name inference

New settings:

```env
XKIRO_API_KEY=
XKIRO_TEXT_MODEL=
XKIRO_VISION_MODEL=
XKIRO_REQUEST_TIMEOUT_SEC=60.0
```

If an xKiro API key is configured and `xkiro` is selected for a route, the corresponding model must be non-empty. If the key is absent, the xKiro factory returns no slots and Chainnode can continue alone.

## Live qualification policy

Add `scripts/probe_xkiro.py`. It must query `/v1/models` before model probes and verify the configured candidate exists. For zero-cost fallback qualification it must require:

- `access_tier == "free"`
- `pricing.input == 0`
- `pricing.output == 0`
- `capabilities.tools == true`
- `capabilities.vision == true` for the vision candidate

The probe must exercise plain chat, structured tool call, tool continuation, and optional vision. It should print machine-readable JSON records so current/future models such as newly added DeepSeek revisions can be qualified without a source-code change.

## B.AI removal

Delete active B.AI-specific artifacts:

- `app/ai/bai.py`
- `scripts/probe_bai.py`
- `tests/test_bai_provider.py`
- `tests/test_bai_probe.py`
- `docs/BAI_INTEGRATION.md`

Remove B.AI from `PROVIDER_FACTORIES`, `Settings`, `.env.example`, active runtime docs, and deployment examples. Historical design/audit snapshots under `docs/superpowers/` may retain B.AI references because they are non-runtime historical records.

The shared `OpenAICompatProvider.force_tool_choice_none_when_no_tools` hook should also be removed if a repository-wide search confirms B.AI is its only consumer. Fresh-synthesis tests should assert that tools and structured tool history are absent, not that a B.AI-only `tool_choice=none` field is injected.

## Environment migration

Removing B.AI from `.env.example` will naturally drop `BAI_*` keys when `scripts/sync_env.py` rewrites `.env`. B.AI credentials must never be copied to `XKIRO_API_KEY`; they are credentials for different services.

Provider-order values need explicit migration so a live deployment does not preserve an unregistered `bai` token. If either provider order contains `bai`, canonicalize the order to put `chainnode` first and `xkiro` second, preserving any other custom provider names afterward without duplicates.

Examples:

```text
bai                 -> chainnode,xkiro
chainnode,bai       -> chainnode,xkiro
bai,chainnode       -> chainnode,xkiro
chainnode           -> chainnode
```

Deployment must add `XKIRO_API_KEY` and qualified xKiro model IDs before relying on fallback traffic.

## Non-goals

- Do not change router retry budgets or provider-health semantics.
- Do not increase the one-image vision limit in this migration.
- Do not dynamically rotate xKiro models in runtime.
- Do not query xKiro catalog on every request or at startup.
- Do not change the qualified Chainnode primary model split.
