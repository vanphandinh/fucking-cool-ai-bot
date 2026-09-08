# NVIDIA NIM integration

NVIDIA NIM is integrated as a first-class OpenAI-compatible provider. Text routing is config-driven and uses the same health, cooldown, tool budget, fallback, statistics, and `/status` behavior as the existing providers.

## Configuration

```env
NVIDIA_NIM_API_KEY=
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_NIM_TEXT_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
NVIDIA_NIM_VISION_MODEL=google/gemma-4-31b-it
NVIDIA_NIM_ENABLE_THINKING=1
NVIDIA_NIM_MAX_TOKENS=4096
NVIDIA_NIM_THINKING_TOKEN_BUDGET=2048
TEXT_PROVIDER_ORDER=nvidia,groq,cloudflare,openrouter,gemini
```

The default base URL is configurable so the same adapter can point to NVIDIA-hosted, commercial, or self-hosted NIM deployments without changing application routing code.

The default text model is `nvidia/nemotron-3.5-lightning-30b-a3b`. It replaces the earlier DeepSeek V4 Flash default because the bot prioritizes interactive Telegram latency while still requiring reliable tool calling. Thinking stays enabled by default; it is bounded instead of disabled globally:

- `NVIDIA_NIM_ENABLE_THINKING=1` keeps reasoning available for multi-step and tool-heavy requests.
- `NVIDIA_NIM_THINKING_TOKEN_BUDGET=2048` caps the hidden reasoning portion.
- `NVIDIA_NIM_MAX_TOKENS=4096` caps reasoning plus the visible answer.

These generation controls are applied only when the selected NVIDIA text model is the repository's Nemotron 3.5 Lightning default. A custom NVIDIA text model or the vision slot is not forced to accept Lightning-specific request fields.

## Existing `.env` migration

The repository's `.env` synchronization workflow preserves existing values. Therefore an existing deployment that pins the old model will continue to run that model after `git pull` unless you update it explicitly.

For the latency-tuned defaults, set:

```env
NVIDIA_NIM_TEXT_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
NVIDIA_NIM_ENABLE_THINKING=1
NVIDIA_NIM_MAX_TOKENS=4096
NVIDIA_NIM_THINKING_TOKEN_BUDGET=2048
```

Then rebuild/recreate the bot container and verify the effective settings. Do not overwrite the NVIDIA API key when syncing `.env.example` changes.

## Runtime behavior

The NVIDIA adapter explicitly sends `stream=false` because the bot consumes a complete JSON Chat Completions response rather than SSE streaming.

A `202 Accepted` response is treated as a transient pending condition. The request falls through to the next provider instead of polling inside the Telegram request lifecycle. Standard `429`, `Retry-After`, `5xx`, `401`, and `403` behavior is handled by the shared provider health layer.

Network failures now include the concrete httpx exception class and elapsed request time. For example, a response that previously appeared only as `nvidia: lỗi mạng ()` can now identify `ReadTimeout`, `ConnectTimeout`, or another transport error and show how long the request ran. This is diagnostic only; the application still treats network errors as transient and preserves the existing fallback behavior.

Do not raise `REQUEST_TIMEOUT_SEC` merely to hide a slow-provider symptom. The default remains 60 seconds so an unhealthy or overloaded primary can fall back instead of consuming the entire Telegram question deadline.

Tool calling uses the existing application tools (`web_search`, `image_search`, `fetch_url`) and the shared tool-call/round budget. NVIDIA-specific reasoning metadata is removed before cross-provider fallback by the router's portable-message cleanup.

## Text routing

With a valid NVIDIA key and the default order, text requests attempt:

```text
NVIDIA NIM / nvidia/nemotron-3.5-lightning-30b-a3b
  -> Groq
  -> Cloudflare
  -> OpenRouter
  -> Gemini
```

If `NVIDIA_NIM_API_KEY` is empty, NVIDIA is omitted and the existing provider pool continues unchanged.

To roll back NVIDIA without changing code:

```env
TEXT_PROVIDER_ORDER=groq,cloudflare,openrouter,gemini
```

or remove `NVIDIA_NIM_API_KEY`.

## Vision routing

NVIDIA vision support exists but is intentionally not enabled in the default `VISION_PROVIDER_ORDER`. This keeps the current production vision behavior unchanged until the configured NIM vision model has been validated with the bot's JPEG/PNG/WebP and multi-image workflows.

To opt in after validation:

```env
VISION_PROVIDER_ORDER=nvidia,groq_qwen38,cloudflare,groq_qwen36,gemini
```

The NVIDIA vision slot uses `NVIDIA_NIM_VISION_MODEL` and the existing OpenAI-compatible `image_url`/Base64 data-URI payload generated at the provider boundary.

## Latency validation

CI remains offline and does not use a real NVIDIA credential. After deploying the new defaults, validate from the same VPS/network path used by the bot and compare at least:

- simple Vietnamese question latency;
- a technical question;
- one `web_search` tool round;
- one `fetch_url` tool round;
- a multi-round tool request;
- timeout/fallback behavior.

For each request, inspect the selected provider and any timeout/transport class in logs. A real production-path benchmark is more useful than increasing timeout values without evidence.

## Free/trial endpoint caveat

Do not assume NVIDIA hosted free/trial endpoints are licensed for a production Telegram service. NVIDIA's trial/developer endpoints are intended for development, research, prototyping, and testing unless your account/deployment has separate production entitlement. For production, use an appropriate NVIDIA commercial/self-hosted entitlement or keep NVIDIA out of the production provider order.

The application does not detect account billing or licensing state.

## Verification

CI must stay fully offline with mocked provider responses. Required regression coverage includes:

- NVIDIA primary text order and missing-key fallback;
- Nemotron Lightning default and bounded-reasoning payload;
- concrete network timeout diagnostics;
- explicit `stream=false`;
- `202` transient handling;
- provider cooldown/fallback behavior;
- tool calling and shared tool budget;
- cross-provider metadata cleanup;
- opt-in vision routing;
- full existing regression suite, dependency audit, and production Docker build.
