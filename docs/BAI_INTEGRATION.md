# B.AI integration

B.AI is integrated as an **opt-in OpenAI-compatible provider**. Runtime uses only
`POST https://api.b.ai/v1/chat/completions`; it does not switch the promoted
models to the Responses API and it does not inject provider-specific reasoning
parameters into production requests.

## Supported promoted models

The integration intentionally allowlists the models that B.AI documented as
0-Credit promotions when this support was added on 2026-09-09:

| Model | Text | Vision slot | Notes |
| --- | --- | --- | --- |
| `qwen3.8-flash` | yes | yes | Recommended initial text/vision candidate. Thinking is enabled by the upstream model by default. |
| `mimo-v2.5` | yes | yes | Multi-turn tool use may return `reasoning_content`; runtime preserves it. |
| `hy3` | yes | no | Text-only in this integration. |
| `glm-5.3-flash` | yes | yes | Thinking cannot be disabled upstream; benchmark latency before promoting it in the order. |

The promotion is not treated as permanent free-tier entitlement. Check B.AI's
current promotions/pricing page before changing production routing:

- https://docs.b.ai/llmservice/promotions-and-pricing-notices/
- https://docs.b.ai/llmservice/api/

## Why reasoning knobs are not sent by runtime

Individual model pages describe upstream controls such as `enable_thinking`,
`thinking`, or `reasoning_effort`, but the generic B.AI Chat Completions schema
has not guaranteed all of those fields as accepted passthrough parameters.
Sending them blindly can turn a working provider into an HTTP 400 failure.

The production provider therefore sends the same conservative payload as the
repo's shared OpenAI-compatible provider:

```json
{
  "model": "qwen3.8-flash",
  "messages": [],
  "tools": []
}
```

The shared response parser already preserves `reasoning_details`, `reasoning`,
and `reasoning_content` when a provider returns them, which is required for
some reasoning/tool-call continuations.

## Configuration

B.AI remains disabled unless `bai` is explicitly added to the relevant order.
Merely setting an API key does not instantiate the provider.

```env
BAI_API_KEY=...
BAI_TEXT_MODEL=qwen3.8-flash
BAI_VISION_MODEL=qwen3.8-flash
BAI_REQUEST_TIMEOUT_SEC=30.0

TEXT_PROVIDER_ORDER=groq,bai,cloudflare,openrouter,gemini
VISION_PROVIDER_ORDER=groq_qwen38,cloudflare,bai,groq_qwen36,gemini
```

The B.AI vision slot is deliberately advertised with `max_images=1` even though
other providers can accept more. Increase that only after a live multi-image
compatibility test demonstrates the exact B.AI wire contract for the chosen
model.

## Live compatibility probe

The probe is manual and never runs in CI. It reads `BAI_API_KEY` from the
environment and never prints the Authorization header.

Baseline text models:

```bash
BAI_API_KEY=... python scripts/probe_bai.py
```

Include function/tool calling:

```bash
BAI_API_KEY=... python scripts/probe_bai.py --tools
```

Probe a JPEG/PNG/WebP against multimodal models:

```bash
BAI_API_KEY=... python scripts/probe_bai.py --image ./probe.jpg
```

Try upstream-style reasoning knobs **only as compatibility experiments**:

```bash
BAI_API_KEY=... python scripts/probe_bai.py --experimental-reasoning
```

Experimental reasoning failures are informational and do not determine the
baseline exit status. Do not move any experimental field into runtime until a
live B.AI request confirms it and an offline regression test protects the exact
accepted payload.

## Rollout checklist

1. Run `GET /v1/models` through the probe and confirm the configured model ID is visible.
2. Run baseline text on all intended models.
3. Run `--tools` and confirm the tool continuation succeeds for the chosen model.
4. For vision, run a one-image JPEG/PNG/WebP probe before adding `bai` to `VISION_PROVIDER_ORDER`.
5. Keep B.AI behind an existing stable provider initially and compare latency/error rate.
6. Only promote B.AI earlier in the order after measuring production-representative p50/p95 latency and 429/5xx rate.
7. Re-check B.AI promotions/pricing periodically; zero-Credit status is promotional.

## Failure behavior

B.AI uses the shared provider health/fallback implementation:

- `401`/`403`: provider is disabled for the process because credentials or access are invalid.
- `429`: provider enters cooldown, honoring `Retry-After` when present.
- `5xx`: treated as transient and eligible for health cooldown/fallback.
- unsupported/invalid `400` requests are not treated as transient network failures.

Because there is only one B.AI text slot and one B.AI vision slot, an upstream
B.AI outage cannot cause the router to retry four B.AI models sequentially
before reaching another provider.
