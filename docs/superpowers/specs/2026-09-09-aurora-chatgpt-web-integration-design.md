# Aurora ChatGPT Web Integration Design

## Status

Proposed design for user review before implementation planning.

## Baseline

This design starts from `main` commit `e9c1cfaf3fdecfdb714c701d544ea027fa31c1fe`, which merged PR #37 (`fix: fresh qwen synthesis after tool budget exhaustion`). The current bot already has a generic `OpenAICompatProvider`, capability-aware routing, request-wide tool budgets, provider health/cooldown state, B.AI-first defaults, multimodal message construction, Docker Compose sidecars, and Python 3.11/3.12 CI.

Aurora is integrated as an external ChatGPT Web gateway, not vendored into the Python application. The pinned initial upstream baseline is Aurora `v2.6.3` (released 2026-09-05). Aurora exposes OpenAI-style `/v1/chat/completions`, `/v1/models`, tool-calling emulation, ChatGPT session/refresh-token account pools, and Docker images published to `ghcr.io/aurora-develop/aurora` with version tags.

## Core decision

Run Aurora as a private Docker sidecar and add a thin `OpenAICompatProvider` adapter in the bot.

The bot talks only to Aurora's OpenAI-compatible service API. ChatGPT Web credentials remain entirely inside the Aurora service.

Target flow:

```text
Telegram
   |
   v
fucking-cool-ai-bot
   |
   v
AIProviderRouter
   |
   +--> B.AI (primary)
   |
   +--> Aurora (ChatGPT Web fallback)
              |
              v
         chatgpt.com
```

Aurora is not a replacement for the router and is not embedded into the bot process.

## Alternatives considered

### 1. Private Aurora sidecar — chosen

Run Aurora in the existing Compose project without publishing a host port. The bot reaches it over the private Compose network at `http://aurora:8080/v1`.

Advantages:

- Keeps reverse-engineered ChatGPT Web logic outside the bot codebase.
- Matches the existing private-sidecar pattern already used for SearXNG and Crawl4AI.
- Keeps ChatGPT session credentials out of Python settings and logs.
- Makes rollback simple: remove `aurora` from provider order or stop the profile.
- Allows Aurora to be upgraded independently.

Trade-off:

- Adds one external runtime service and one additional upstream failure domain.

### 2. Remote/shared Aurora gateway — not the default

Run Aurora on another host and configure `AURORA_BASE_URL=https://.../v1`.

This can be useful if multiple applications share one gateway, but it expands the network/security boundary and requires TLS, firewalling, and remote secret management. The integration should remain compatible with this topology, but production defaults in this repository use the private sidecar.

### 3. Vendor or reimplement Aurora inside the bot — rejected

Do not copy Aurora source, reproduce its ChatGPT Web fingerprint/session logic in Python, or make the bot responsible for ChatGPT Web authentication.

This would tightly couple the bot to unstable private web behavior, greatly expand maintenance scope, and make upstream Aurora fixes harder to consume.

## Goals

1. Add Aurora as an optional OpenAI-compatible text provider.
2. Keep B.AI as the first text provider by default.
3. Place Aurora immediately after B.AI when configured.
4. Keep the integration isolated behind the existing provider abstraction.
5. Run Aurora privately in Docker Compose with no host-published port.
6. Keep ChatGPT Web session/refresh/access tokens out of bot environment variables.
7. Use a separate Aurora service key between the bot and Aurora.
8. Preserve the existing router tool loop and fresh-synthesis behavior.
9. Allow Aurora tool-calling emulation to participate in the existing tool executor flow.
10. Treat Aurora authentication/upstream-account failures differently from permanent static API-key failures.
11. Provide offline regression tests plus a credentialed manual smoke probe.
12. Make rollback possible without reverting unrelated bot changes.

## Non-goals

- Do not vendor or fork Aurora source into this repository.
- Do not make Aurora publicly reachable from the host or Internet by default.
- Do not pass ChatGPT access/session/refresh tokens through bot requests.
- Do not use Aurora `ENABLE_EXTERNAL_TOKEN` mode.
- Do not use Aurora to preserve conversation history; the bot remains the conversation-context owner.
- Do not migrate the bot from Chat Completions to the Responses API in this change.
- Do not add Aurora image generation, image editing, audio, file upload, or TTS/STT integration.
- Do not add Aurora vision routing in v1 of this integration.
- Do not delete Gemini, Groq, Cloudflare, or OpenRouter as part of this change. A separate provider-cleanup change may do that independently.
- Do not change B.AI model selection, Fresh Qwen Synthesis semantics, search backends, Crawl4AI, or tool budgets.
- Do not attempt to bypass Aurora or ChatGPT upstream rate limits.

## Provider integration

Create a focused provider factory, expected at:

```text
app/ai/aurora.py
```

The factory reuses `OpenAICompatProvider`; it does not implement a second HTTP client.

Expected settings:

```text
AURORA_BASE_URL=http://aurora:8080/v1
AURORA_API_KEY=
AURORA_MODEL=auto
AURORA_REQUEST_TIMEOUT_SEC=90.0
```

`AURORA_API_KEY` is the Aurora service access key configured through Aurora's `Authorization` environment setting. It is not a ChatGPT account token.

Aurora is considered configured only when both a non-empty base URL and service key are present. `AURORA_MODEL=auto` is the initial default because Aurora supports the `auto` model selector and can obtain the upstream model list dynamically. The model remains explicitly configurable so a deployment can pin a ChatGPT Web model after validation.

The v1 provider advertises text capability only:

```text
route=text
supports_vision=false
max_images=0
```

Vision is intentionally deferred until a production-shaped image probe validates Aurora's inline-image conversion against the bot's current `data:image/...;base64,...` format and the selected ChatGPT subscription/model.

## Provider order

The integration must be non-destructive to the current provider pool.

When all current providers remain present, the default text order becomes:

```text
bai,aurora,gemini,groq,cloudflare,openrouter
```

Aurora is skipped automatically when it is not configured.

If a separate provider-cleanup change lands first and removes other providers, the intended final order is simply:

```text
bai,aurora
```

The Aurora implementation must rebase on the latest `main` and adapt to the provider set that actually exists at implementation time. It must not resurrect providers intentionally removed by another accepted change.

## Aurora runtime configuration

Add an opt-in Compose service named `aurora`, under profile `aurora`.

Initial image policy:

```text
AURORA_IMAGE=ghcr.io/aurora-develop/aurora:v2.6.3
```

Use a versioned image instead of `latest`. Keep `AURORA_IMAGE` overridable so upgrades can be tested without editing Compose.

Recommended Aurora environment:

```text
SERVER_HOST=0.0.0.0
SERVER_PORT=8080
Authorization=${AURORA_API_KEY}
FREE_ACCOUNTS=false
ENABLE_EXTERNAL_TOKEN=false
ENABLE_HISTORY=false
TOOL_CALLING_ENABLED=true
STREAM_MODE=false
REFUSAL_RETRIES=3
```

Rationale:

- `Authorization` creates a bot-to-gateway service boundary.
- `FREE_ACCOUNTS=false` avoids silently falling back to anonymous device pools.
- `ENABLE_EXTERNAL_TOKEN=false` prevents callers from supplying arbitrary ChatGPT tokens through the gateway.
- `ENABLE_HISTORY=false` prevents Aurora from becoming a second conversation-memory owner.
- `TOOL_CALLING_ENABLED=true` enables compatibility with the bot's existing tools.
- `STREAM_MODE=false` matches the bot's current non-streaming provider contract and avoids unnecessary SSE complexity.

Do not publish `8080` to the host. The bot uses the Compose DNS name `aurora`.

Do not add `depends_on` that makes the Telegram bot fail to start when Aurora is unavailable. Aurora is a fallback provider; service failure must degrade through router fallback rather than block the entire bot process.

## ChatGPT credential boundary

Aurora owns ChatGPT credentials. The bot owns only the Aurora service key.

Preferred initial credential file:

```text
aurora/session_tokens.txt
```

Alternative supported deployment:

```text
aurora/refresh_tokens.txt
```

Credential files are bind-mounted read-only to Aurora and must be ignored by Git.

Required ignore coverage:

```text
aurora/session_tokens.txt
aurora/refresh_tokens.txt
aurora/access_tokens.txt
```

Do not commit example values that look like real tokens. Documentation should instruct the operator to create the credential file locally with restrictive filesystem permissions.

The bot must never call Aurora `/auth/session` or `/auth/refresh` during normal request handling. Aurora's own account manager owns token renewal.

## Tool calling

The existing bot already owns tool execution:

```text
model -> tool_calls -> bot executes tools -> role=tool -> model -> final answer
```

Aurora does not execute tools. It emulates OpenAI function calling by injecting a text calling convention into ChatGPT Web prompts and parsing the model output back into OpenAI-format `tool_calls`.

The integration therefore reuses the current router loop unchanged wherever possible.

Required behavior:

- Send the existing OpenAI-style `tools` schema to Aurora.
- Accept Aurora `choices[0].message.tool_calls` through the existing parser.
- Execute tools only in the bot.
- Replay tool results using the existing `role=tool` messages.
- Keep the current request-wide `MAX_TOOL_ROUNDS`, `_MAX_TOOL_CALLS_TOTAL`, and `_MAX_PARALLEL_TOOL_CALLS` behavior unchanged.
- Preserve Fresh Qwen Synthesis logic; Aurora receives the same compact post-budget fallback context if it is reached after discovery exhaustion.
- Never execute raw `<tool_call>` markup directly. Existing defensive parsing/error behavior remains the safety boundary.

Because Aurora tool calling is emulated rather than native ChatGPT Web function calling, this is the highest-risk functional area and receives dedicated regression plus manual smoke coverage.

## Authentication and health semantics

The current generic provider health policy permanently disables a provider in-process after HTTP 401/403. That is appropriate for static API keys but too aggressive for Aurora because a gateway request can fail due to an upstream ChatGPT account/session state that Aurora may later refresh.

Introduce a narrowly-scoped provider health policy switch rather than special-casing provider names in the router.

Conceptual behavior:

```text
normal provider:
  401/403 -> permanently unavailable for current process

Aurora:
  401/403 -> temporary cooldown, retain eligibility for later retry
```

The default behavior for all existing providers must remain unchanged.

A wrong `AURORA_API_KEY` will therefore cause repeated cooldown/retry cycles rather than an in-process permanent disable. This is preferable to making the bot inspect Aurora-specific error bodies that may change upstream. Logs/status must retain the last error so configuration mistakes remain visible.

Suggested Aurora auth cooldown: 60 seconds unless a usable `Retry-After` header is present.

429 and 5xx behavior continues to use the existing generic transient/cooldown logic.

## Error and fallback behavior

Aurora is a normal router candidate, not a terminal dependency.

Expected flow with the current provider pool:

```text
B.AI success
  -> return

B.AI genuine failure
  -> Aurora

Aurora success
  -> return

Aurora network / 401 / 403 / 429 / 5xx / malformed response
  -> record health state
  -> continue to later configured provider
```

If a separate cleanup leaves only B.AI and Aurora, failure of both produces the existing `AllProvidersFailed` behavior.

Do not retry Aurora repeatedly inside one provider turn beyond behavior already owned by the router/tool loop. Aurora itself may perform its documented internal refusal retry behavior.

## Conversation ownership

The bot remains the sole owner of user conversation history.

Aurora must run with:

```text
ENABLE_HISTORY=false
```

Every request remains self-contained from the bot's perspective. This avoids double-history, hidden state divergence between provider fallbacks, and conversation leakage across Telegram users.

## Security

Security constraints are mandatory:

- Aurora has no host-published port by default.
- ChatGPT credential files are read-only mounts and Git-ignored.
- `AURORA_API_KEY` is a random service secret distinct from ChatGPT credentials.
- `ENABLE_EXTERNAL_TOKEN=false`.
- Bot logs must never include authorization headers or ChatGPT token file contents.
- Error excerpts continue to use existing response-size/redaction boundaries.
- No token is placed in README examples, tests, fixtures, or committed `.env` files.
- The deployment guide must state that unofficial ChatGPT Web integration can break when upstream web behavior changes and should not be treated as an official OpenAI API contract.

## Observability

Reuse existing provider name and health reporting rather than adding a new monitoring subsystem.

Aurora provider names:

```text
aurora
```

Required observable states through existing mechanisms:

- provider selected for successful answer,
- fallback count,
- last provider error,
- cooldown/unavailable state,
- 429 retry delay where available.

Do not log ChatGPT account IDs, session tokens, refresh tokens, or access tokens.

## Manual smoke probe

Add a small operator-facing script following the existing probe/smoke pattern, expected at:

```text
scripts/probe_aurora.py
```

The script reads only:

```text
AURORA_BASE_URL
AURORA_API_KEY
AURORA_MODEL
```

It must never read ChatGPT token files directly.

Probe modes:

1. `/v1/models` authentication/connectivity check.
2. Simple non-tool chat completion.
3. Optional deterministic tool-call schema probe that verifies Aurora returns a parseable OpenAI-format tool call without executing the tool.

The probe must redact secrets and keep response excerpts bounded.

For the private Compose topology, documentation should run the probe from the bot container/network rather than publishing Aurora to localhost.

## Testing strategy

All runtime behavior changes use TDD.

### Provider factory/config tests

Verify:

- Aurora is absent when no service key is configured.
- `AURORA_BASE_URL` normalizes correctly through `OpenAICompatProvider`.
- `AURORA_MODEL` and timeout are configurable.
- Aurora advertises text-only capability in v1.
- `configured_provider_names` includes Aurora in the configured order.
- duplicate order slots remain deduplicated.

### Request/response compatibility tests

Using mocked HTTP responses, verify:

- request path is `/v1/chat/completions`,
- service key is sent as Bearer auth through the existing client,
- normal text content parses correctly,
- OpenAI-format Aurora tool calls parse correctly,
- tool results can complete the existing router loop,
- malformed Aurora tool output never executes as a tool.

### Health-policy regressions

Verify:

- existing providers still permanently disable after 401/403,
- Aurora 401/403 uses temporary cooldown instead,
- Aurora becomes eligible again after cooldown,
- 429 respects `Retry-After`,
- 5xx retains current transient failure semantics.

### Router regressions

Verify:

- B.AI remains first when both B.AI and Aurora are configured,
- Aurora is used after B.AI failure,
- later providers remain reachable after Aurora failure in the current multi-provider baseline,
- Fresh Qwen post-budget compact synthesis/fallback behavior is unchanged,
- tool budget is request-wide across a B.AI -> Aurora fallback.

### Compose/security tests

Add an offline regression around `docker-compose.yml` that verifies:

- Aurora is opt-in under profile `aurora`,
- image is version-pinned/overridable,
- no `ports:` entry exposes Aurora,
- ChatGPT token mounts are read-only,
- `ENABLE_EXTERNAL_TOKEN=false`,
- `ENABLE_HISTORY=false`,
- `FREE_ACCOUNTS=false`.

CI must not require real ChatGPT credentials and must not pull/run Aurora for unit tests.

### Full verification

Implementation completion requires:

```text
python -m unittest <targeted Aurora tests> -v
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m compileall -q app tests scripts
docker compose --profile aurora config --quiet
docker build --tag fcai-audit:aurora .
```

GitHub Actions must pass on Python 3.11 and 3.12.

A credentialed Aurora smoke probe is a manual deployment gate, not a CI requirement.

## Documentation changes

Update:

- `.env.example` with Aurora service settings only.
- `README.md` provider/deployment sections.
- Docker deployment instructions describing the `aurora` profile and local credential-file creation.
- Security/operational note explaining that Aurora is an unofficial ChatGPT Web gateway and may break after upstream web changes.

Do not place ChatGPT tokens in `.env.example`.

## Upgrade policy

Do not track Aurora `latest` automatically.

Initial baseline:

```text
v2.6.3
```

For later upgrades:

1. change `AURORA_IMAGE` in deployment or the pinned default,
2. run Compose config validation,
3. run the manual model/chat/tool smoke probe,
4. verify bot fallback behavior,
5. only then promote the new version.

This isolates ChatGPT Web breakage from unrelated bot releases.

## Rollout plan

Roll out in three operational stages without changing code between stages:

### Stage 1 — dark configuration

Deploy Aurora sidecar and validate `/v1/models` + simple chat through `scripts/probe_aurora.py`, while Aurora is not yet in the active production `TEXT_PROVIDER_ORDER`.

### Stage 2 — fallback canary

Use:

```text
TEXT_PROVIDER_ORDER=bai,aurora,...
```

Keep B.AI primary. Observe Aurora only when B.AI genuinely falls back. Validate latency, tool-call success, auth refresh, and cooldown behavior.

### Stage 3 — steady-state fallback

Keep Aurora as the first fallback if the canary is stable. Do not promote Aurora ahead of B.AI in this plan.

## Rollback

Fast rollback requires no code revert:

```text
remove aurora from TEXT_PROVIDER_ORDER
```

Then stop the sidecar profile if desired.

The bot must continue operating with all previously configured providers after Aurora is removed from the order.

## Expected file impact

Expected new files:

```text
app/ai/aurora.py
tests/test_aurora_provider.py
tests/test_aurora_health.py
tests/test_aurora_compose.py
scripts/probe_aurora.py
docs/superpowers/plans/2026-09-09-aurora-chatgpt-web-integration.md
```

Expected modified files:

```text
app/config.py
app/ai/base.py or app/ai/health.py
app/ai/router.py
.env.example
.gitignore
docker-compose.yml
README.md
tests/test_provider_order.py
.github/workflows/audit.yml only if targeted Aurora checks need an explicit CI step
```

The implementation plan may refine exact test-file placement, but it must preserve the boundaries and non-goals in this design.

## Acceptance criteria

The integration is complete when all of the following are true:

1. With Aurora unconfigured, bot behavior and provider order are unchanged except for the inert optional slot.
2. With B.AI + Aurora configured, B.AI remains primary and Aurora is the next eligible text provider.
3. Aurora receives only its service key from the bot; ChatGPT session credentials never enter Python settings.
4. Aurora is not exposed on a host port in the default Compose deployment.
5. Normal Aurora text answers work through the existing OpenAI-compatible provider parser.
6. A full Aurora tool round-trip works through the existing bot tool executor.
7. Aurora 401/403 does not permanently disable the provider in-process.
8. Existing provider 401/403 semantics are unchanged.
9. Existing Fresh Qwen Synthesis regressions continue to pass.
10. All offline tests, Ruff, compile checks, Compose validation, Docker build, and Python 3.11/3.12 GitHub Actions pass.
11. A credentialed manual smoke test validates `/v1/models`, simple chat, and one tool-call schema against the deployed Aurora sidecar.
12. Rollback is possible by removing `aurora` from `TEXT_PROVIDER_ORDER` and stopping the sidecar.
