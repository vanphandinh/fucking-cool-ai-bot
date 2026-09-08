# X/Twitter content fetching

## Runtime path

For a direct public X/Twitter status URL, `fetch_url` now resolves content before considering web search:

1. FxTwitter API v2 `GET /2/status/{id}`.
2. When the user explicitly asks for the full thread, `GET /2/thread/{id}` first, then focal-status fallback.
3. Official `https://publish.x.com/oembed` fallback.
4. Existing generic Jina/direct HTML reader as the last content-fetch fallback.
5. `web_search` remains an AI decision only after direct fetch is unavailable or insufficient.

The AI tool surface stays small: `web_search`, `image_search`, and `fetch_url`. `fetch_url` accepts optional `mode=auto|x_thread` instead of adding X-specific tool names.

## Safety

- User-supplied URLs are parsed only for an allowlisted host and numeric status ID.
- Specialized API hosts are fixed in code (`api.fxtwitter.com` and `publish.x.com`); user input cannot redirect API egress.
- Deceptive hosts such as `x.com.evil.example` are not treated as X URLs.
- Existing generic SSRF protection remains unchanged for generic page fetching.
- User-facing sources stay canonical `https://x.com/.../status/<id>` URLs, never API or mirror URLs.

## Limits

- Specialized resolver total deadline: `min(12s, REQUEST_TIMEOUT_SEC)`.
- X tool content is capped at 5500 characters before the router's existing tool-output cap.
- Threads are capped at 12 posts and report truncation.
- Raw media CDN URLs are excluded from the LLM context; media type and alt text are retained when available.
- Duplicate `(url, mode)` calls are cached within one Telegram question.
- Production should keep `MAX_TOOL_ROUNDS=2`; do not raise tool rounds to mask content-fetch failures.

## Rollback

Set in `.env`:

```dotenv
X_FETCH_ENABLED=0
```

Then recreate the bot container. This bypasses FxTwitter/oEmbed and returns direct X links to the existing generic reader path without a code rollback.

## VPS smoke test

```bash
docker compose --profile searxng exec -T bot python - <<'PY'
import asyncio
from app.config import Settings
from app.search.url_service import read_url

URL = "https://x.com/TrustlessState/status/2097054140350554620"
result = asyncio.run(read_url(URL, Settings(), mode="auto"))
print("ok:", result.ok)
print("backend:", result.backend)
print("source:", result.source_url)
print("preview:", result.text[:300].replace("\n", " "))
PY
```

For a still-public post, normal success should report `fxtwitter_status` or `x_oembed` while `source` remains canonical `x.com`.

## Deferred adapters

The initial integration intentionally does not add paid X API credentials, Firecrawl, Bright Data, Apify, or TwitterAPI.io. If production metrics show FxTwitter + oEmbed are insufficient, add another adapter behind the existing `UrlReadResult` interface so the orchestrator/tool contract does not need to change again.
