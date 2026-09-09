# B.AI integration

B.AI is integrated as an OpenAI-compatible provider and is the **first default
provider when a B.AI key is configured**. Runtime uses only
`POST https://api.b.ai/v1/chat/completions`; it does not switch the promoted
models to the Responses API and it does not inject provider-specific reasoning
parameters into production requests.

## Supported promoted models

The integration intentionally allowlists the models that B.AI documented as
0-Credit promotions when this support was added on 2026-09-09:

| Model | Text | Vision slot | Notes |
| --- | --- | --- | --- |
| `qwen3.8-flash` | yes | yes | Default text/vision model; live text, tool-call, continuation, and vision probes passed. |
| `mimo-v2.5` | yes | yes | Multi-turn tool use may return `reasoning_content`; runtime preserves it. |
| `hy3` | yes | no | Text-only in this integration. |
| `glm-5.3-flash` | yes | yes | Thinking cannot be disabled upstream; measured tool latency was higher than Qwen in the initial live probe. |

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

The default routing priority is B.AI first and Gemini second. Providers without
valid credentials are skipped automatically, so leaving `BAI_API_KEY` empty
simply removes B.AI from the effective runtime order. Supplying a valid B.AI key
activates B.AI in the first slot without requiring a separate order edit.

```env
BAI_API_KEY=...
BAI_TEXT_MODEL=qwen3.8-flash
BAI_VISION_MODEL=qwen3.8-flash
BAI_REQUEST_TIMEOUT_SEC=30.0

TEXT_PROVIDER_ORDER=bai,gemini,groq,cloudflare,openrouter
VISION_PROVIDER_ORDER=bai,gemini,groq_qwen38,cloudflare,groq_qwen36
```

The order remains configurable. Remove `bai` from the relevant order to disable
B.AI routing without deleting the key.

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

Successful chat responses include a bounded `content_preview` field in the
probe output when assistant text is present. This makes image probes useful for
manual semantic verification rather than merely proving that the API accepted
an image payload.

HTTP `429` responses are retried sequentially. The probe honors numeric
`Retry-After` values when present; otherwise it uses exponential backoff with
jitter. Retry behavior is bounded and configurable:

```bash
BAI_API_KEY=... python scripts/probe_bai.py \
  --max-retries 3 \
  --retry-base-delay 1.0
```

The elapsed time reported for a request includes any rate-limit sleep/retry
cycle. When a retry occurred, the output also includes `retry_count`.

Try upstream-style reasoning knobs **only as compatibility experiments**:

```bash
BAI_API_KEY=... python scripts/probe_bai.py --experimental-reasoning
```

Experimental reasoning failures are informational and do not determine the
baseline exit status. Do not move any experimental field into runtime until a
live B.AI request confirms it and an offline regression test protects the exact
accepted payload.

## Initial live validation

Manual sequential probes with a real B.AI key confirmed HTTP 200 for baseline
text and tool-call continuation on all four supported promotional models. The
initial single-run observations were approximately:

- `qwen3.8-flash`: text 1.46s; tool call 0.96s; continuation 1.34s.
- `hy3`: text 1.71s; tool call 1.40s; continuation 1.43s.
- `glm-5.3-flash`: text 2.74s; tool call 6.09s; continuation 6.45s.
- `mimo-v2.5`: text 4.61s; tool call 6.19s; continuation 4.79s.

A one-image `qwen3.8-flash` probe also returned HTTP 200 in about 1.42s and
correctly described the synthetic image as red on the left and blue on the
right. These are compatibility observations, not latency SLAs.

## Rollout checklist

1. Run `GET /v1/models` through the probe and confirm the configured model ID is visible.
2. Run baseline text on all intended models.
3. Run `--tools` and confirm the tool continuation succeeds for the chosen model.
4. For vision, run a one-image JPEG/PNG/WebP probe and inspect `content_preview`.
5. Keep `qwen3.8-flash` as the initial B.AI text/vision model unless newer measurements justify changing it.
6. Monitor production-representative p50/p95 latency plus 429/5xx rate, especially because B.AI is now first in the default order.
7. Re-check B.AI promotions/pricing periodically; zero-Credit status is promotional.

## Failure behavior

B.AI uses the shared provider health/fallback implementation:

- `401`/`403`: provider is disabled for the process because credentials or access are invalid.
- `429`: provider enters cooldown, honoring `Retry-After` when present.
- `5xx`: treated as transient and eligible for health cooldown/fallback.
- unsupported/invalid `400` requests are not treated as transient network failures.

The manual probe has a separate bounded retry loop for `429` so rate limiting is
not mistaken for a model-contract failure during compatibility testing. This
probe retry behavior does not change runtime provider behavior.

Because there is only one B.AI text slot and one B.AI vision slot, an upstream
B.AI outage cannot cause the router to retry four B.AI models sequentially
before reaching Gemini or another provider.
