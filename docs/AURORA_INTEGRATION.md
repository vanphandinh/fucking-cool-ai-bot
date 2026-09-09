# Aurora integration

Aurora is an **optional text-only fallback provider** registered through the generic provider-family framework introduced by PR #38.

Current production registry:

```text
bai
  - text
  - vision (max_images=1)

aurora
  - text only
```

Current default orders:

```env
TEXT_PROVIDER_ORDER=bai,aurora
VISION_PROVIDER_ORDER=bai
```

Gemini, Groq, OpenRouter, and Cloudflare Workers AI remain removed from source/runtime. Aurora does not restore any of those providers.

## Architecture

Aurora is integrated through `PROVIDER_FACTORIES`, not through provider-name branches in the router:

```text
Settings
  -> text/vision provider orders
  -> PROVIDER_FACTORIES
       -> bai factory
       -> aurora factory
  -> AIProvider slots
  -> generic AIProviderRouter
```

`app/ai/aurora.py` reuses `OpenAICompatProvider` because the private gateway exposes OpenAI-style Chat Completions. The core router remains provider-agnostic and returns the PR #38 `CompletionResult` object.

Aurora advertises:

- route: `text`
- vision: disabled
- `max_images=0`
- model default: `auto`
- request timeout: 90 seconds

## Authentication boundary

There are two distinct credential layers:

1. `AURORA_API_KEY`: internal bot-to-Aurora service authentication.
2. ChatGPT Web session/access/refresh credentials: consumed only by the Aurora sidecar via a read-only local file.

Never put ChatGPT session/access/refresh credentials in `.env` or Python settings.

Default deployment variables:

```env
AURORA_BASE_URL=http://aurora:8080/v1
AURORA_API_KEY=
AURORA_MODEL=auto
AURORA_REQUEST_TIMEOUT_SEC=90.0
AURORA_IMAGE=ghcr.io/aurora-develop/aurora:v2.6.3
AURORA_CREDENTIAL_FILE=./aurora/session_tokens.txt
AURORA_CREDENTIAL_TARGET=/home/nonroot/session_tokens.txt
```

Credential files are Git-ignored:

```text
aurora/session_tokens.txt
aurora/refresh_tokens.txt
aurora/access_tokens.txt
```

## Private sidecar

Prepare the local credential directory:

```bash
mkdir -p aurora
chmod 700 aurora
# create aurora/session_tokens.txt locally, one valid token per line
chmod 600 aurora/session_tokens.txt
```

Start only the private profile:

```bash
docker compose --profile aurora up -d aurora
```

The Aurora service has no published host port. The bot reaches it through the Compose network at `http://aurora:8080/v1`.

The Compose boundary pins `ghcr.io/aurora-develop/aurora:v2.6.3` and sets:

```env
FREE_ACCOUNTS=false
ENABLE_EXTERNAL_TOKEN=false
ENABLE_HISTORY=false
TOOL_CALLING_ENABLED=true
STREAM_MODE=false
REFUSAL_RETRIES=3
```

Refresh-token alternative:

```env
AURORA_CREDENTIAL_FILE=./aurora/refresh_tokens.txt
AURORA_CREDENTIAL_TARGET=/refresh_tokens.txt
```

## Routing and health

Aurora is skipped when `AURORA_API_KEY` is empty. With both providers configured, the text path is:

```text
B.AI -> Aurora
```

Vision remains B.AI-only.

Default providers preserve the existing PR #38 health semantics:

- 401/403: disable until process restart;
- 429: cooldown, honoring numeric `Retry-After`;
- repeated transient failures: cooldown according to generic health policy.

Aurora opts into a 60-second temporary cooldown for 401/403 instead of permanent disable. A numeric `Retry-After` overrides the 60-second default. This behavior is configured through generic `ProviderHealth`/`OpenAICompatProvider` parameters rather than a router special case.

## Tool calling

Aurora uses the existing generic tool loop:

```text
Aurora tool_calls
  -> bot executes approved tool
  -> role=tool + tool_call_id
  -> Aurora final response
```

No Aurora-specific tool executor or router loop exists. Malformed/unknown raw tool markup is rejected by the shared OpenAI-compatible parser.

## Offline verification

CI runs Aurora-specific tests on Python 3.11 and 3.12 before the full regression suite:

```bash
python -m unittest discover -s tests -p 'test_aurora*.py' -v
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
python -m ruff check .
python -m compileall -q app tests scripts
```

Python 3.12 CI also validates the private Compose profile with a dummy credential file and builds the production image. CI does **not** start Aurora or use a real ChatGPT credential.

## Live canary before merge/deploy

Run from the bot image/private Compose network:

```bash
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode models
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode chat
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode tool
```

Then exercise one controlled B.AI provider-local failure in staging and verify:

- Aurora is selected as fallback;
- fallback observability reports `bai -> aurora`;
- one safe tool round-trip completes;
- ChatGPT credentials never appear in bot logs;
- Aurora auth cooldown recovers without restarting the bot.

## Rollback

Remove Aurora from the text order and stop the profile:

```env
TEXT_PROVIDER_ORDER=bai
```

```bash
docker compose --profile aurora stop aurora
```

Because Aurora is registered as an independent provider family, this rollback does not change B.AI, vision, search, URL reading, Fresh Synthesis, or tool-budget behavior.

## Upstream risk

Aurora is an unofficial ChatGPT Web gateway. ChatGPT Web request/auth behavior can change independently of this repository, so live canary validation is required before rollout after upstream changes.
