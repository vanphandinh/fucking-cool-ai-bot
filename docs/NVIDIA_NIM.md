# NVIDIA NIM integration

NVIDIA NIM is integrated as a first-class OpenAI-compatible provider. Text routing is config-driven and uses the same health, cooldown, tool budget, fallback, statistics, and `/status` behavior as the existing providers.

## Configuration

```env
NVIDIA_NIM_API_KEY=
NVIDIA_NIM_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_NIM_TEXT_MODEL=deepseek-ai/deepseek-v4-flash-0731
NVIDIA_NIM_VISION_MODEL=google/gemma-4-31b-it
TEXT_PROVIDER_ORDER=nvidia,groq,cloudflare,openrouter,gemini
```

The default base URL is configurable so the same adapter can point to NVIDIA-hosted, commercial, or self-hosted NIM deployments without changing application routing code.

## Runtime behavior

The NVIDIA adapter explicitly sends `stream=false` because the bot consumes a complete JSON Chat Completions response rather than SSE streaming.

A `202 Accepted` response is treated as a transient pending condition. The request falls through to the next provider instead of polling inside the Telegram request lifecycle. Standard `429`, `Retry-After`, `5xx`, `401`, and `403` behavior is handled by the shared provider health layer.

Tool calling uses the existing application tools (`web_search`, `image_search`, `fetch_url`) and the shared tool-call/round budget. NVIDIA-specific reasoning metadata is removed before cross-provider fallback by the router's portable-message cleanup.

## Text routing

With a valid NVIDIA key and the default order, text requests attempt:

```text
NVIDIA NIM -> Groq -> Cloudflare -> OpenRouter -> Gemini
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

## Free/trial endpoint caveat

Do not assume NVIDIA hosted free/trial endpoints are licensed for a production Telegram service. NVIDIA's trial/developer endpoints are intended for development, research, prototyping, and testing unless your account/deployment has separate production entitlement. For production, use an appropriate NVIDIA commercial/self-hosted entitlement or keep NVIDIA out of the production provider order.

The application does not detect account billing or licensing state.

## Verification

CI must stay fully offline with mocked provider responses. Required regression coverage includes:

- NVIDIA primary text order and missing-key fallback;
- explicit `stream=false`;
- `202` transient handling;
- provider cooldown/fallback behavior;
- tool calling and shared tool budget;
- cross-provider metadata cleanup;
- opt-in vision routing;
- full existing regression suite, dependency audit, and production Docker build.
