# Crawl4AI URL Reading Integration

Crawl4AI is an optional private URL-rendering backend for `fetch_url`. It does **not** replace search.

```text
Search discovery: SearXNG -> DDGS fallback
URL reading:      X-specific -> Crawl4AI -> generic reader
```

The production path uses `POST /crawl`. Do not switch production to `/md?f=fit`, `/llm`, or `/ask`: Crawl4AI 0.9.3 may resolve an LLM provider for non-raw Markdown filtering, which would create an unwanted second LLM path.

## Security invariants

- image pinned to `unclecode/crawl4ai:0.9.3`;
- no host `11235` port mapping;
- bearer token required between bot and Crawl4AI;
- bot validates the user URL with `validate_public_url()` before the private service call;
- redirected/canonical URLs returned by Crawl4AI are trusted only after the same public-URL validation;
- `CRAWL4AI_EXECUTE_JS_ENABLED=false`;
- `CRAWL4AI_HOOKS_ENABLED=false`;
- no OpenAI/Gemini/Groq/B.AI/Cloudflare/OpenRouter credentials are passed to Crawl4AI;
- the private-service HTTP client uses `trust_env=False`;
- `asyncio.CancelledError` propagates without generic fallback;
- each `fetch_url` tries Crawl4AI at most once and the generic reader at most once.

## Configuration

Generate a random token:

```bash
openssl rand -hex 32
# put value in .env as CRAWL4AI_API_TOKEN
```

Recommended values:

```dotenv
CRAWL4AI_ENABLED=1
CRAWL4AI_URL=http://crawl4ai:11235
CRAWL4AI_API_TOKEN=<random-secret>
CRAWL4AI_TIMEOUT_SEC=25.0
CRAWL4AI_MAX_CHARS=12000
CRAWL4AI_IMAGE=unclecode/crawl4ai:0.9.3
CRAWL4AI_SHM_SIZE=512m
```

If `CRAWL4AI_ENABLED=1` but URL or token is blank, the bot logs a warning and keeps using the generic reader instead of failing startup.

## Deploy

```bash
docker compose --profile searxng --profile crawl4ai pull
docker compose --profile searxng --profile crawl4ai up -d --build

docker compose ps
docker compose exec bot python scripts/smoke_crawl4ai.py
```

`crawl4ai` has no hard `depends_on` relationship from the bot. The bot remains functional when the Crawl4AI profile is stopped or unavailable.

For an additional JS-heavy representative page:

```bash
CRAWL4AI_SMOKE_JS_URL=https://example-js-heavy-site.invalid \
  docker compose exec bot python scripts/smoke_crawl4ai.py
```

Use a real public representative URL instead of the placeholder above.

## Runtime behavior

For an ordinary public page:

1. bot performs SSRF/public-URL validation;
2. X/Twitter specialized handling runs first for X status URLs;
3. canonical ordinary URL is sent once to `POST /crawl` with a fixed server-authored payload;
4. successful content preference is `fit_markdown`, then `raw_markdown`, then normalized `cleaned_html`;
5. timeout, network error, 401/403, 429, 5xx, malformed JSON, failed result, or empty content causes exactly one fallback to the existing generic reader;
6. cancellation does not launch fallback work.

The bot does not forward arbitrary browser config, hooks, JavaScript, provider settings, output paths, callback URLs, or model credentials.

## Rollback

Immediate application rollback:

```dotenv
CRAWL4AI_ENABLED=0
```

Restart the bot after changing `.env`:

```bash
docker compose restart bot
```

The Crawl4AI container can then be stopped independently:

```bash
docker compose --profile crawl4ai stop crawl4ai
```

Search remains unchanged during rollback.

## Troubleshooting

### Bot warns that token or URL is missing

Check `.env` and restart the bot. Missing Crawl4AI configuration intentionally degrades to the generic reader.

### 401 / 403

Verify the same `CRAWL4AI_API_TOKEN` is present in the bot environment and Crawl4AI service environment. Do not log or paste the token into issue reports.

### Timeouts / high fallback rate

Inspect container CPU/RAM, `CRAWL4AI_TIMEOUT_SEC`, target-site behavior, and representative fetch latency. Do not increase the timeout beyond `QUESTION_TIMEOUT_SEC`; configuration rejects impossible timeout budgets.

### High memory usage

Measure on the target VPS before increasing `CRAWL4AI_SHM_SIZE`. The initial default is `512m`; it is an operational knob, not a guarantee of memory consumption.

## Failure injection before promotion

Verify all of these while the bot stays functional:

```text
Crawl4AI stopped          -> generic reader succeeds
wrong token               -> 401/403 -> generic reader succeeds
Crawl4AI timeout          -> generic reader succeeds
CRAWL4AI_ENABLED=0        -> Crawl4AI is never called
private/localhost URL     -> blocked before both readers
X status URL              -> specialized X reader remains first
bot cancellation          -> no generic fallback is launched
```

Confirm representative logs contain only coarse reasons and never contain the bearer token or full upstream payload.

## Promotion metrics

For at least 20 representative URLs, record:

```text
Crawl4AI success rate
fallback-to-generic rate
p50/p95 fetch_url latency
container RAM/CPU peak
static vs JS-heavy extraction quality
```

Do not invent fixed VPS thresholds before measurement. Promotion requires no availability regression, no unbounded memory growth, and material improvement on cases where the generic reader performs poorly.

## Version promotion policy

For any upgrade from 0.9.3:

1. read upstream release/security notes;
2. change the pinned image on a branch;
3. run offline tests;
4. run the live smoke against representative static and JS-heavy pages;
5. inspect RAM/CPU, latency, and fallback rate;
6. promote only after successful observation;
7. never switch production directly to `latest`.
