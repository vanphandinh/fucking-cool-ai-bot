# Crawl4AI URL Reading Design

## Objective

Integrate Crawl4AI as the preferred renderer/extractor for ordinary public web pages while preserving the existing search and URL-reading reliability layers. SearXNG remains the primary search backend, DDGS remains the bounded fallback, the X/Twitter-specific reader keeps priority for X status URLs, and the current generic SSRF-safe reader remains the final fallback.

## Architecture

```text
User
  -> Bot LLM
      -> web_search
          -> existing Search Router
              -> SearXNG primary
              -> DDGS bounded fallback
      -> fetch_url
          -> validate_public_url()
          -> X/Twitter specialized reader when applicable
          -> Crawl4AI client for ordinary public URLs
          -> existing generic reader on Crawl4AI failure
          -> normalized bounded text to LLM
```

Crawl4AI is not a search backend and does not replace `app/search/router.py`, `service.py`, `searxng_backend.py`, `ddgs_backend.py`, cache, circuit breaker, singleflight, or source policy. It is a URL-reading backend only.

## Crawl4AI deployment

- Pin Crawl4AI to `unclecode/crawl4ai:0.9.3` for the initial rollout; never use `latest` in production.
- Run Crawl4AI on the same private Docker network as the bot.
- Do not publish port `11235` to the host.
- Configure a strong `CRAWL4AI_API_TOKEN` so the container may bind on the Docker network while still requiring `Authorization: Bearer <token>`.
- The bot calls `http://crawl4ai:11235` by default.
- Keep `CRAWL4AI_EXECUTE_JS_ENABLED=false` and `CRAWL4AI_HOOKS_ENABLED=false` because neither capability is required for URL reading.
- Do not configure any OpenAI/Gemini/Groq/etc. key inside Crawl4AI.

## API choice

Do not make `/md` with `fit`, `bm25`, or `llm` the production path in Crawl4AI 0.9.3. Upstream `handle_markdown_request()` resolves an LLM provider for every filter except `raw`, which would introduce an unnecessary second AI-provider configuration and violate the no-double-LLM constraint.

Use `POST /crawl` with one URL and trusted, minimal crawler configuration. Read the first successful result and prefer, in order:

1. `markdown.fit_markdown` when present and non-empty;
2. `markdown.raw_markdown` when present and non-empty;
3. `cleaned_html` converted/normalized only if the previous fields are unavailable;
4. otherwise classify the Crawl4AI call as unusable and fall back to the existing generic reader.

The integration must tolerate upstream response shape differences where `markdown` may be a dictionary or a string-like serialized value. Parsing is defensive and never treats malformed success responses as usable.

## Bot-side configuration

Add these settings:

- `CRAWL4AI_ENABLED=1`
- `CRAWL4AI_URL=http://crawl4ai:11235`
- `CRAWL4AI_API_TOKEN=`
- `CRAWL4AI_TIMEOUT_SEC=25.0`
- `CRAWL4AI_MAX_CHARS=12000`

Rules:

- `CRAWL4AI_ENABLED=0` is the immediate rollback switch.
- If enabled but URL or token is blank, log a startup warning and skip Crawl4AI rather than failing bot startup.
- Timeout must be positive and must remain below `QUESTION_TIMEOUT_SEC`; validation should reject an impossible configuration.
- `CRAWL4AI_MAX_CHARS` bounds text passed back to the model independently from upstream response size.

## URL routing behavior

`read_url(url, settings, mode)` keeps the current external contract and `UrlReadResult` type.

Routing order:

1. Validate the user URL using the existing `reader.validate_public_url()` before any network call.
2. If the URL is an X/Twitter status and the X reader is enabled, preserve the existing specialized resolver behavior.
3. Resolve the canonical ordinary URL.
4. If Crawl4AI is enabled and correctly configured, call Crawl4AI.
5. If Crawl4AI returns usable text, return backend `crawl4ai`.
6. On timeout, connection error, 401/403, 429, 5xx, malformed JSON, unsuccessful result, or empty extracted content, log a bounded warning and call the existing generic reader once.
7. Never retry Crawl4AI within the same `fetch_url` call.
8. Cancellation must propagate; do not convert `asyncio.CancelledError` into a fallback request.

## SSRF and trust boundaries

The existing bot-side `validate_public_url()` remains mandatory before invoking Crawl4AI. This preserves the current fail-closed validation semantics for malformed, loopback, private, link-local, numeric, and unsupported URLs.

Crawl4AI 0.9.3 also performs server-side destination validation and includes recent SSRF hardening. Defense in depth is intentional; neither side replaces the other.

The bot never accepts or forwards arbitrary Crawl4AI browser configuration, hooks, JavaScript, output paths, provider names, API endpoints, or LLM credentials from the user/model. The client payload is server-authored and fixed.

## Runtime and connection management

Create a focused `app/search/crawl4ai_client.py` module and keep its HTTP client process-local and reusable. Extend the existing search runtime only if it stays clear that the Crawl4AI client is URL-reading state rather than search-routing state; otherwise use a small dedicated runtime in the new module. In either case shutdown must close the shared `httpx.AsyncClient` from `app/main.py`.

The client must:

- send bearer authentication;
- use `trust_env=False` so ambient proxy variables do not redirect the private service call;
- enforce the configured timeout;
- cap/read JSON defensively;
- produce a small typed result such as `Crawl4AIReadResult(text, source_url, ok, reason)`;
- never expose the token in logs or returned text.

## Docker Compose

Add `crawl4ai` as an opt-in Compose profile, preferably `profiles: ["crawl4ai"]`, similar to SearXNG. The bot should not depend hard on the container so rollout/rollback can occur independently.

Use a healthcheck against `/health`. The health endpoint is unauthenticated upstream and is suitable for container health. Do not publish ports.

Recommended service policy:

- image pinned to `unclecode/crawl4ai:0.9.3` or `${CRAWL4AI_IMAGE:-unclecode/crawl4ai:0.9.3}`;
- `restart: unless-stopped`;
- `init: true`;
- `shm_size: 512m` initially, adjustable after VPS measurement;
- pass `CRAWL4AI_API_TOKEN` from `.env`;
- disable execute-JS and hooks explicitly;
- no LLM provider environment variables.

## Testing strategy

Unit tests must cover:

- successful Crawl4AI result is preferred over generic reader;
- X specialized reader still wins before Crawl4AI;
- feature flag bypasses Crawl4AI;
- missing URL/token bypasses Crawl4AI;
- invalid/private URL is rejected before Crawl4AI;
- 401, timeout, network error, 429/5xx, malformed JSON, `success=false`, empty result all fall back exactly once;
- cancellation propagates without generic fallback;
- bearer token is present but never leaks in exceptions/loggable result text;
- output is truncated to `CRAWL4AI_MAX_CHARS`;
- upstream `redirected_url`/canonical URL is accepted only when it remains a valid public URL; otherwise preserve the original safe URL;
- orchestrator still marks successful `fetch_url` as searched/source and still caches the same URL once per question;
- Compose contains no Crawl4AI host port mapping and pins a version.

Add an opt-in live smoke script/test, excluded from ordinary CI, that hits the private service and verifies a static HTML page plus one JS-heavy page.

## Observability and rollout

Log only backend outcome and coarse reason, for example `crawl4ai timeout -> generic fallback`; never log bearer tokens or full upstream payloads.

Initial rollout:

1. deploy Crawl4AI profile privately;
2. verify health and resource usage without enabling it in the bot;
3. enable `CRAWL4AI_ENABLED=1` for real traffic;
4. compare successful fetch rate, fallback rate, latency, RAM, and representative extracted content against the generic reader;
5. rollback instantly with `CRAWL4AI_ENABLED=0` if latency/resource/error rate is unacceptable.

## Non-goals

- Do not replace SearXNG/DDGS search routing.
- Do not implement automatic search-result scraping in this change.
- Do not add Crawl4AI LLM extraction, `/llm`, `/ask`, hooks, execute-JS, screenshot, or PDF-generation tools to the model.
- Do not add Ketch, Firecrawl, another search provider, reranking, or RRF in the same change.
- Do not remove the current generic reader.
