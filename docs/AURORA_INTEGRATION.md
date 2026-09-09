# Aurora ChatGPT Web integration

Aurora is an optional, **unofficial** ChatGPT Web gateway used as the first text fallback after B.AI. It runs as a private Docker Compose sidecar and exposes an OpenAI-compatible `/v1/chat/completions` API to the bot.

## Runtime boundary

```text
Telegram -> fucking-cool-ai-bot -> AIProviderRouter
                                |-> B.AI (primary)
                                `-> Aurora (fallback) -> chatgpt.com
```

The Python bot knows only the Aurora service endpoint and service key:

```env
AURORA_BASE_URL=http://aurora:8080/v1
AURORA_API_KEY=<private bot-to-Aurora secret>
AURORA_MODEL=auto
AURORA_REQUEST_TIMEOUT_SEC=90.0
```

ChatGPT `session_token`, `refresh_token`, and `access_token` values are **not** bot settings. They belong only to the Aurora container via a read-only local credential file.

## Prepare credentials

Default session-token deployment:

```bash
mkdir -p aurora
chmod 700 aurora
# Create aurora/session_tokens.txt locally, one valid token per line.
chmod 600 aurora/session_tokens.txt
```

The three supported local credential filenames are Git-ignored:

```text
aurora/session_tokens.txt
aurora/refresh_tokens.txt
aurora/access_tokens.txt
```

To use a refresh-token file instead of the default session-token file:

```env
AURORA_CREDENTIAL_FILE=./aurora/refresh_tokens.txt
AURORA_CREDENTIAL_TARGET=/refresh_tokens.txt
```

Never commit any of these files or copy their contents into `.env`.

## Generate the service key

Generate a separate high-entropy secret for `AURORA_API_KEY`, for example:

```bash
openssl rand -hex 32
```

This value authenticates the bot to Aurora. It is not a ChatGPT token.

## Start Aurora privately

The repository pins Aurora to `v2.6.3` by default:

```env
AURORA_IMAGE=ghcr.io/aurora-develop/aurora:v2.6.3
```

Start only the Aurora profile:

```bash
docker compose --profile aurora up -d aurora
```

The service intentionally has no `ports:` mapping. The bot reaches it through Docker DNS at `http://aurora:8080/v1`.

Aurora runtime defaults are intentionally restrictive:

```text
FREE_ACCOUNTS=false
ENABLE_EXTERNAL_TOKEN=false
ENABLE_HISTORY=false
TOOL_CALLING_ENABLED=true
STREAM_MODE=false
REFUSAL_RETRIES=3
```

`ENABLE_HISTORY=false` is required because the bot remains the sole owner of conversation history.

## Smoke probe

The production bot image contains `scripts/probe_aurora.py`, so live checks can run from the same private Compose network without exposing Aurora to the host.

```bash
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode models
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode chat
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode tool
```

The tool probe requests an `echo_probe` tool call but **never executes it**. It only verifies that Aurora converts the ChatGPT Web response into a parseable OpenAI-format `tool_calls` item.

## Routing

Default text order when all providers are configured:

```env
TEXT_PROVIDER_ORDER=bai,aurora,gemini,groq,cloudflare,openrouter
```

Aurora is skipped automatically if `AURORA_API_KEY` is empty or if `aurora` is removed from the order.

Aurora v1 is text-only. Vision, Responses API, files, image generation/editing, TTS, STT, and audio routing are intentionally outside this integration.

## Health and fallback

Existing static providers retain their current behavior: HTTP 401/403 disables that provider until process restart.

Aurora is different because its downstream ChatGPT account/session may be refreshed by the gateway. Aurora 401/403 therefore creates a temporary 60-second cooldown (or honors a numeric `Retry-After`) instead of permanently disabling the provider. HTTP 429 and 5xx retain the generic provider-health behavior.

Tool execution always remains in the bot:

```text
Aurora tool_calls -> bot executes allowed tool -> role=tool -> Aurora -> final text
```

Aurora emulates function calling for ChatGPT Web. The bot still rejects malformed or unknown raw `<tool_call>` markup rather than executing it.

## Rollout

Recommended rollout:

1. Start Aurora and run `models`, `chat`, and `tool` probes while it is not active in `TEXT_PROVIDER_ORDER`.
2. Enable `bai,aurora,...` so B.AI remains primary and Aurora is exercised only on genuine fallback.
3. Observe latency, auth refresh, tool-call success, and cooldown recovery before treating Aurora as a steady fallback.

Do not promote Aurora ahead of B.AI as part of this integration.

## Rollback

Fast rollback requires no code revert:

1. Remove `aurora` from `TEXT_PROVIDER_ORDER`.
2. Restart/recreate the bot if needed for environment changes.
3. Stop the sidecar:

```bash
docker compose --profile aurora stop aurora
```

## Upgrade policy

Do not track Aurora `latest` automatically. For an upgrade:

1. Change `AURORA_IMAGE` to an explicit version.
2. Validate Compose.
3. Run all three smoke modes.
4. Exercise one controlled B.AI -> Aurora fallback and one tool round-trip.
5. Promote only after those checks pass.

Aurora wraps private ChatGPT Web behavior, not an official OpenAI API contract. Upstream ChatGPT Web changes can break it without notice, so keep the gateway isolated and independently replaceable.
