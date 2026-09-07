# Telegram vision input

Bot now supports image understanding without changing the existing text route.

## Supported input

- Telegram photo (normalized to JPEG)
- JPEG/PNG/WebP sent as document
- Image on the current message
- Image in the replied message
- Up to `MAX_IMAGES_PER_REQUEST` images, with per-image and total byte limits

Raw image bytes live only in the in-memory `UserRequest`. Base64/data URLs are created only at the provider boundary and are never stored in `ChatMemory`.

## Vision providers

Configured independently from the text pool with `VISION_PROVIDER_ORDER`:

1. Gemini vision
2. Groq Qwen vision slot 1
3. Groq Qwen vision slot 2
4. Cloudflare Workers AI reserve

Set `VISION_ENABLED=0` for config-only rollback. Removing a slot from `VISION_PROVIDER_ORDER` disables that fallback without changing code.

## Telegram UX

- `[photo] + caption "@bot ..."`
- reply to a photo + `@bot ...`
- reply to a photo + `/ask ...`
- PNG/WebP document + caption mentioning the bot

A plain group image without a caption/mention still does not trigger the bot.

## Resilience

- 429: use `Retry-After`, otherwise 60s cooldown
- 401/403: disable the slot until process restart
- repeated network/5xx failures: cooldown after two transient failures
- shared tool-call budget is preserved across provider fallback

## Verification

Run:

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Then smoke-test Telegram with OCR screenshot, UI screenshot, chart, ordinary photo, and image + web-search request before production rollout.
