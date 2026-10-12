# Crawl4AI URL Reading Integration

Crawl4AI is an optional private URL-rendering backend for `fetch_url`. It does **not** replace search.

```text
Search discovery: SearXNG -> DDGS fallback
URL reading:      X-specific -> Crawl4AI -> generic reader
```

The production path uses `POST /crawl`. Do not switch production to `/md?f=fit`, `/llm`, or `/ask`: Crawl4AI 0.9.3 may resolve an LLM provider for non-raw Markdown filtering, which would create an unwanted second LLM path.

## Security invariants

- image pinned to `unclecode/crawl4ai:0.9.3`;
- Crawl4AI is disabled by default and requires the Linux staging isolation gate before activation;
- the pinned 0.9.3 REST API rejects client-supplied proxy fields (HTTP 400);
  the client sends a fixed config without proxy fields; server-side egress
  enforcement creates a localhost DNS-pinning proxy which forwards through
  Squid via `CRAWL4AI_UPSTREAM_PROXY`;
- the crawler has only private Docker networks;
- the proxy alone attaches to the uplink, with host firewall rules blocking non-public destinations;
- no host `11235` port mapping;
- bearer token required between bot and Crawl4AI;
- bot validates the user URL with `validate_public_url()` before the private service call;
- redirected/canonical URLs returned by Crawl4AI are trusted only after the same public-URL validation;
- `CRAWL4AI_EXECUTE_JS_ENABLED=false`;
- `CRAWL4AI_HOOKS_ENABLED=false`;
- no OpenAI/Gemini/Groq/B.AI/Cloudflare/OpenRouter credentials are passed to Crawl4AI;
- the private-service HTTP client uses `trust_env=False`;
- Crawl4AI response bodies are streamed and capped at 4 MiB before JSON parsing;
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
CRAWL4AI_ENABLED=0
CRAWL4AI_URL=http://crawl4ai:11235
CRAWL4AI_API_TOKEN=<random-secret>
CRAWL4AI_EGRESS_PROXY_URL=http://crawl4ai-proxy:3128
CRAWL4AI_TIMEOUT_SEC=25.0
CRAWL4AI_MAX_CHARS=12000
CRAWL4AI_IMAGE=unclecode/crawl4ai:0.9.3
CRAWL4AI_PROXY_IMAGE=ubuntu/squid:6.10-24.10_edge@sha256:e0a69e6c6f6a9f5bf1fb4edd6afa9c0aec23ea89ff8f3077dd5c55e7772438f0
CRAWL4AI_SHM_SIZE=512m
```

If enabled but the token, fixed crawler endpoint, or fixed proxy endpoint is absent or incorrect, the bot logs a coarse warning and keeps using the generic reader. The timeout budget check is enforced for a configured crawler. No user-supplied browser configuration is accepted.

### Crawl4AI 0.9.3 REST compatibility gate (October 2026)

An isolated Docker Desktop test with the pinned image reproduced HTTP 400 for
`CrawlerRunConfig.proxy_config`. Removing that field alone still returned HTTP
400 for `BrowserConfig.proxy_config`. Both are forbidden by the REST API's
`Provenance.UNTRUSTED` boundary. The REST client now omits both fields.

Source inspection confirmed the image's `/app/api.py` calls `enforce_egress()`
on the `/crawl` browser configuration, and `/app/server.py` starts a localhost
DNS-pinning proxy first. `/app/egress_broker.py` assigns that proxy to new
browser configs; `/app/egress_proxy.py` resolves and pins each destination,
then sends absolute-form HTTP requests or pinned-IP HTTPS tunnels to Squid.
The permanent browser created at startup does not visibly call
`enforce_egress()`, so this additional path must be treated cautiously.
Keep `CRAWL4AI_ENABLED=0` until the full staging SSRF test suite passes,
including zero unexpected internal sink requests and browser reloads.

The isolated firewall check succeeds with an explicit rule allowing only
established return packets from the uplink to Squid. Testing from the
Docker-in-Docker host itself is not a valid substitute for testing from the
crawler container: the separate host INPUT guard also blocks host-originated
responses.

### Observed Docker Desktop isolation results (2026-10-12)

The pinned image was recreated inside an isolated Docker-in-Docker engine with
`CRAWL4AI_UPSTREAM_PROXY=http://crawl4ai-proxy:3128`. The test client used the
real `POST /crawl` endpoint. All URLs belonged to disposable fixtures within
that engine. No production containers or external websites were tested.

| Test | Observed result |
| --- | --- |
| Public fixture (`11.23.0.50:80`) through Squid | `/crawl` success, extracted fixture text |
| Private IP, loopback, metadata IP | REST seed validation rejected each (`400`) |
| Public fixture redirecting to private sink | `/crawl` returned `success=False` |
| Public fixture with private image/iframe | `/crawl` returned `success=False` |
| Direct crawler-to-private connection bypassing proxy | Connection timed out |
| Squid stopped during public `/crawl` | `success=False`, no direct-connect fallback observed |
| Squid restarted, firewall reapplied and checked | Public `/crawl` succeeded again |
| `read_url()` during a real Squid outage | Routed once to `generic_reader` with a controlled stub, returned `ok=True` |
| Controlled DNS name resolving public, then private | Two distinct DNS A responses observed; `/crawl` failed, sink received no request |
| Crawler restarted, firewall rechecked | Healthy; public `/crawl` succeeded again, private redirect/subresource crawls failed |
| Private sink after these probes | No unexpected requests in container log |

The documented observations are specific to the isolated iptables backend.
The rebinding fixture recorded `rebinda.fcai.test` resolving first to the
controlled public fixture (`11.23.0.50`) and then to the private sink
(`172.19.0.4`) with zero TTL; the crawler rejected the request before the
private sink received HTTP. This is evidence for the tested resolution sequence,
not a substitute for repeating the probe on the actual staging host. The
original Docker resolver was restored before the restart probes. The direct
Squid probe returned `403` for the private sink. A failed crawl of
the HTML subresource fixture demonstrates refusal, but does **not** establish
that harmless page content can still be extracted when a blocked resource is
present. The permanent browser pool needs additional coverage for reuse across
multiple pages, even though post-restart REST checks passed.

The bot-level outage probe imported the project `Settings` and `read_url()`
inside the isolated Crawl4AI image and contacted its actual `/crawl` endpoint.
With Squid stopped, Crawl4AI returned `crawl_failed`; the generic-reader
function was replaced by a controlled `AsyncMock`, which ran exactly once and
returned a successful `UrlReadResult`. With Squid restored, `read_url()` used
the real crawler and never called that stub. This proves fallback dispatch,
**not** successful live generic HTTP retrieval on the staging bot network.

**Still required:** repeat DNS rebinding and positive/negative subresource
instrumentation on the target staging host, validate nftables if that backend
is used, verify bot-level generic-reader fallback during a live proxy outage,
test permanent-browser reuse, and run the representative 20-URL smoke test.
Keep `CRAWL4AI_ENABLED=0` until these gates pass.

For the existing isolated Docker-in-Docker setup on Windows, collect further
read-only evidence with:

```powershell
pwsh -NoProfile -File .\scripts\crawl4ai_docker_diagnostics.ps1
```

The report excerpts `/app/egress_broker.py`, `/app/egress_proxy.py`,
`/app/api.py`, `/app/crawler_pool.py` and server startup from the image.
Review it for secrets before sharing.
This source inspection is separate from the mandatory network isolation tests.

## Deploy

Leave `CRAWL4AI_ENABLED=0` on the initial deploy. On a dedicated **Linux staging VPS** with Docker Compose and root firewall access:

```bash
docker compose --profile crawl4ai pull
docker compose --profile crawl4ai up -d --build
sudo bash scripts/crawl4ai_firewall.sh apply
sudo bash scripts/crawl4ai_firewall.sh check
docker compose ps
```

The firewall script selects Docker's iptables or nftables backend and checks the network attachments. Re-run `check` **after restarting/recreating containers**; configure firewall persistence in VPS boot provisioning. If backend detection, bridging, rules, or restart verification fails, do **not** enable Crawl4AI.

On public fixture servers **controlled by this project**, configure redirects, HTML image/iframe requests and DNS rebinding to the private test sink. Run:

```bash
python scripts/test_crawl4ai_isolation.py \
  --public-url 'https://controlled.example.test/' \
  --redirect-url 'https://controlled.example.test/redirect?to={sink_url}' \
  --subresource-url 'https://controlled.example.test/subresources?src={sink_url}' \
  --rebind-url 'http://controlled-rebind.example.test/'
```

Substitute real controlled hostnames. Verify **zero sink HTTP requests** in direct-private, redirect, rebinding, image/iframe and proxy bypass cases. Also test loopback, metadata address, proxy outage and crawler timeout, with generic-reader fallback. Complete a sample of **at least 20 representative URLs**; record extraction, fallback, latency and RAM/CPU. Confirm the fixed REST `BrowserConfig`/`CrawlerRunConfig` proxy shape against the actual Crawl4AI **0.9.3** image. Unit tests do not prove live isolation.

Only after all security and smoke gates pass, set `CRAWL4AI_ENABLED=1` and recreate the bot:

```bash
docker compose up -d --no-deps --force-recreate bot
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
4. response bytes are read with a 4 MiB cap before JSON parsing;
5. successful content preference is `fit_markdown`, then `raw_markdown`, then normalized `cleaned_html`;
6. timeout, network error, 401/403, 429, 5xx, oversized response, malformed JSON, failed result, or empty content causes exactly one fallback to the existing generic reader;
7. cancellation does not launch fallback work.

The bot does not forward arbitrary browser config, hooks, JavaScript, provider settings, output paths, callback URLs, or model credentials. Host isolation blocks network connections before any returned URL is inspected; response URL validation adds defense in depth.

## Rollback

Immediate application rollback:

```dotenv
CRAWL4AI_ENABLED=0
```

Recreate the bot after changing `.env` (a plain restart does not reload the environment):

```bash
docker compose up -d --no-deps --force-recreate bot
```

The Crawl4AI container can then be stopped independently:

```bash
docker compose --profile crawl4ai stop crawl4ai
```

Search remains unchanged during rollback. Keep the Squid and Linux firewall blocks enforced even while Crawl4AI is disabled.

## Troubleshooting

### Bot warns that the required isolation configuration is missing

Check `.env` and recreate the bot. Missing or incorrect fixed endpoints intentionally degrade to the generic reader; the browser running does not prove isolation.

### 401 / 403

Verify the same `CRAWL4AI_API_TOKEN` is present in the bot environment and Crawl4AI service environment. Do not log or paste the token into issue reports.

### Timeouts / high fallback rate

Inspect container CPU/RAM, `CRAWL4AI_TIMEOUT_SEC`, target-site behavior, and representative fetch latency. Do not increase the timeout beyond `QUESTION_TIMEOUT_SEC` while Crawl4AI is active; configuration rejects impossible active timeout budgets.

### High memory usage

Measure on the target VPS before increasing `CRAWL4AI_SHM_SIZE`. The initial default is `512m`; it is an operational knob, not a guarantee of memory consumption. Bot-side Crawl4AI HTTP responses larger than 4 MiB are rejected and fall back to the generic reader.

## Failure injection before promotion

Verify all of these while the bot stays functional:

```text
Crawl4AI stopped          -> generic reader succeeds
Squid proxy stopped       -> crawler failure -> generic reader succeeds
wrong token               -> 401/403 -> generic reader succeeds
Crawl4AI timeout          -> generic reader succeeds
oversized response        -> generic reader succeeds
CRAWL4AI_ENABLED=0        -> Crawl4AI is never called
private/localhost URL     -> blocked before both readers
redirect/rebinding/iframe -> private request sink receives zero HTTP requests
direct browser egress     -> denied by the host firewall
X status URL              -> specialized X reader remains first
bot cancellation          -> no generic fallback is launched
```

Confirm representative logs contain only coarse reasons and never contain the bearer token or full upstream payload.

## Verification gates

CI runs Crawl4AI-specific tests and the full regression suite with Ruff, dependency, compile and Compose checks. The Linux host-firewall and DNS-rebinding probes are mandatory before production activation, and deliberately run on staging with project-controlled fixtures rather than ordinary CI.

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
