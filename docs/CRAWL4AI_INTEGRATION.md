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

## Remote crawler mode (opt-in, HTTPS only)

When the VPS cannot spare the crawler's memory, the crawler may run on another
machine and the bot may reach it through one explicit endpoint:

```dotenv
CRAWL4AI_URL=https://crawl.dn.chainno.de
CRAWL4AI_REMOTE_HOST=crawl.dn.chainno.de
CRAWL4AI_EGRESS_PROXY_URL=
```

`CRAWL4AI_REMOTE_HOST` is the opt-in and must match the URL host exactly. It
must be a bare fully qualified DNS name: no scheme, port, path, IP literal or
`localhost`. Anything else leaves `crawl4ai_ready` false, logs
`Crawl4AI endpoint not approved (mode=… remote_host=…)`, and keeps the generic
reader; the bot still starts.

Operational notes:

- The VPS Squid/CoreDNS/host-firewall layers do not apply to a remote crawler.
  Run Squid beside it with `crawl4ai/squid.conf` and point
  `CRAWL4AI_UPSTREAM_PROXY` at that instance, so the browser cannot reach
  private ranges, Docker peers or the home LAN.
- Put the endpoint behind a TLS reverse proxy that only exposes
  `POST /crawl` plus `GET /health`. Never publish port 11235.
- Docker Desktop rewrites every published-port client address to its bridge
  gateway, so a source-IP allowlist cannot identify the VPS there. In that
  topology the bearer token is the access control; enforce an IP allowlist
  only on a Linux edge that preserves peer addresses.
- A dynamic home IP is fine with DDNS. With a 300 s TTL, an address change can
  cost about five minutes of generic-reader fallback before it recovers.
- Stopping the VPS crawler leaves the `FCAI_CRAWL4AI` chain bound to container
  addresses that no longer exist. If Docker later reuses one of those IPs, that
  container is dropped; flush the chain only as a deliberate firewall change.

## Production activation (operator runbook)

**Current gate: NO-GO until the remaining checks below are verified on the
target host.** GitHub's isolated SSRF workflow and the VPS `SAFE DORMANT`
report do not establish firewall protection throughout the actual VPS Docker
startup interval. A firewall restoration service running *after* Docker starts
also does not, by itself, prove that packets were blocked before the service
applied its rules. If the bot is already enabled without this evidence, use
the [rollback procedure](#rollback) first.

1. **Prepare without activating.** Check out the intended audited release on
   the VPS and review its diff/configuration. Keep `.env` and the running bot at
   `CRAWL4AI_ENABLED=0`. Set the pinned image, private
   `CRAWL4AI_URL=http://crawl4ai:11235`, private
   `CRAWL4AI_EGRESS_PROXY_URL=http://crawl4ai-proxy:3128`, and the *same*
   nonempty `CRAWL4AI_API_TOKEN` for bot and crawler. Do not expose the token.
   Validate the deployment file with `docker compose --profile crawl4ai config
   --quiet`; check that crawler, Squid and CoreDNS have `restart: "no"` and no
   host-published port. A plain bot restart does not reload `.env`.
2. **Qualify on a dedicated staging Docker engine.** Pass the
   [controlled SSRF fixture suite](#deploy) (public redirect to private,
   DNS rebinding, image/iframe subresources, direct egress, browser proxy
   bypass, Squid outage, container restart). Verify the *independent* public
   fixture, DNS, private-sink, Squid and firewall logs; require zero
   unauthorized private-sink requests. An HTTP 403, timeout or `crawl_failed`
   alone is inconclusive. Pass the representative 20-URL smoke test and record
   extraction, fallback, latency and resource usage.
3. **Prove the target-host startup boundary before starting production
   crawler services.** Review the actual Docker firewall backend, network
   attachments, reboot/restart policies and boot service; obtain evidence that
   a default-deny path protects **the entire interval from Docker/container
   startup until the address-bound rules are restored**. Use an authorized
   maintenance window or a faithful isolated host/boot rehearsal. The
   `REBOOT AFTER PASS (SAFE_DORMANT)` result only proves the crawler stayed
   stopped; it does not satisfy this running-service gate. If continuous
   protection cannot be shown, **stop here and keep Crawl4AI disabled**.
4. **Start the vetted profile, only after step 3 passes.** From the release
   directory on the VPS, with the bot still disabled:

   ```bash
   docker compose --profile crawl4ai pull crawl4ai crawl4ai-proxy crawl4ai-dns
   docker compose --profile crawl4ai up -d --no-deps crawl4ai-dns crawl4ai-proxy crawl4ai
   sudo bash scripts/crawl4ai_firewall.sh apply
   sudo bash scripts/crawl4ai_firewall.sh check
   docker compose --profile crawl4ai ps
   sudo python3 scripts/check_crawl4ai_vps_live.py --live
   ```

   `up` creates or replaces real containers and can change their IPs. Applying
   the firewall *after* `up` is **not** a substitute for step 3's proven
   startup-time deny policy. Require each command to pass; repeat firewall
   verification after any recreation. Inspect the report locally before
   sharing it, removing secrets and sensitive URLs.
5. **Activate the bot only after every gate passes.** Change the single
   `.env` assignment to `CRAWL4AI_ENABLED=1`, then run:

   ```bash
   docker compose up -d --no-deps --force-recreate bot
   docker compose cp ./scripts/smoke_crawl4ai.py bot:/tmp/smoke_crawl4ai.py
   docker compose exec -e PYTHONPATH=/app bot python /tmp/smoke_crawl4ai.py
   ```

   Check `docker compose ps`, bot logs, real public page rendering, Squid
   access logs, generic-reader fallback and resource usage. The smoke script
   is copied into the bot container because the production image does not
   include `scripts/`. Recopy it after recreating the bot. Do not rerun the
   disabled-bot `--live` preflight once the bot is enabled.
6. **Rollback on any failure.** Set `CRAWL4AI_ENABLED=0` in `.env` and run
   `docker compose up -d --no-deps --force-recreate bot`. Confirm its effective
   setting is disabled and the generic reader works. Stop the optional
   crawler-side services with `docker compose --profile crawl4ai stop crawl4ai
   crawl4ai-proxy crawl4ai-dns` if isolation is uncertain. Retain firewall
   protections and collect logs for investigation. Do not remove protective
   rules as part of rollback.

Activation remains an explicit operator decision after all gates are evidenced;
this runbook does not authorize a production reboot, firewall change, or
deployment. Further detail: [DNS and egress verification](CRAWL4AI_DNS_EGRESS.md)
and [verification gates](#verification-gates).

### Known limitations

- Container đã tạo từ bản cũ vẫn giữ restart policy cũ cho tới khi chạy
  `scripts/apply_crawl4ai_safe_boot.sh`; thay đổi trong compose chỉ áp dụng cho
  container mới hoặc được recreate.
- Image có permanent startup browser riêng; đường egress đó chưa được chứng minh
  là luôn đi qua `enforce_egress()`. Coi đây là đường chưa được xác minh cho tới
  khi staging startup-boundary gate pass.

## Deploy

Leave `CRAWL4AI_ENABLED=0` on the initial deploy. On a dedicated **Linux staging VPS** with Docker Compose and root firewall access (the following startup sequence must **not** be treated as proven safe for production):

```bash
docker compose --profile crawl4ai pull
docker compose --profile crawl4ai up -d --build
sudo bash scripts/crawl4ai_firewall.sh apply
sudo bash scripts/crawl4ai_firewall.sh check
docker compose ps
```

The firewall script selects Docker's iptables or nftables backend and checks the network attachments. Re-run `check` **after restarting/recreating containers**; configure firewall persistence in VPS boot provisioning. If backend detection, bridging, rules, or restart verification fails, do **not** enable Crawl4AI.

The older `check_crawl4ai_report.sh` entry point remains supported; `verify_crawl4ai_security.sh` wraps it.

On an existing Linux VPS with `CRAWL4AI_ENABLED=0`, run this additional
**non-disruptive live egress probe** without stopping or recreating services:

```bash
set -o pipefail
sudo python3 scripts/check_crawl4ai_vps_live.py --live \
  2>&1 | tee ./crawl4ai-vps-live-egress.txt
echo "live egress exit code: ${PIPESTATUS[0]}"
```

It verifies the running bot remains disabled, checks firewall rules before and
after testing, attempts direct public/private/metadata sockets from the real
crawler container, requires Squid to reach one public URL and return HTTP 403
for private targets, and launches an **unproxied Chromium** navigation that
must not reach the public Internet. It does not modify containers or firewall
rules. Review the report before sharing. A PASS here is only a **partial**
SSRF security gate: controlled sink request logs, redirects, subresources,
DNS rebinding, proxy-outage fallback, and firewall restoration after a VPS
reboot still require dedicated staging verification below.

For a repeatable **dedicated staging VPS** gate, prepare a manifest `staging-urls.txt`
with at least 20 distinct real public pages and four public SSRF fixtures under
project control. A fixture redirect must target `{sink_url}`, the HTML fixture
must load `{sink_url}` as an image/iframe, and the DNS fixture must change from
a public answer to the controlled private sink during the crawl. Independently
confirm the fixture's DNS and request logs show that each attack was exercised;
an ordinary crawler failure alone does not prove DNS rebinding was attempted.
With `.env` and the running bot both explicitly set to `CRAWL4AI_ENABLED=0`, run:

```bash
set -o pipefail
sudo bash scripts/check_crawl4ai_vps_staging.sh --staging \
  --public-url 'https://PUBLIC-FIXTURE.example/' \
  --redirect-url 'https://REDIRECT-FIXTURE.example/?to={sink_url}' \
  --subresource-url 'https://HTML-FIXTURE.example/?src={sink_url}' \
  --rebind-url 'http://DNS-FIXTURE.example/' \
  --urls-file ./staging-urls.txt --restart --recreate \
  2>&1 | tee ./crawl4ai-staging-report.txt
echo "staging exit code: ${PIPESTATUS[0]}"
```

Replace all `.example` placeholders with real fixtures before execution.
The script checks firewall/backend/network identity, restarts and recreates the
three crawler-side containers, reapplies the firewall when container IPs drift,
verifies the private sink receives no unauthorized requests, checks 20+ live
pages, confirms fallback while Squid is stopped, and restores Squid afterward.
The SSRF runner requires a real `read_page()` result for every attempted
request: Python/import errors or a disabled client cannot count as a blocked
URL. Direct private targets must return `blocked_url`; redirect and DNS
rebinding attempts must return `blocked_redirect` or `blocked_url`. A generic
`crawl_failed` is inconclusive and must not count as a protected redirect. The
public HTML fixture containing image/iframe subresources must itself load
successfully. Independently inspect the public fixture's HTTP access log and
the DNS fixture's responses to establish that the redirect, subresource
requests and both DNS answers were actually exercised; a passed sink counter
alone cannot establish this.
Run it only on a dedicated staging Docker engine; it does not enable the bot's
Crawl4AI setting. Check the command's exit status and inspect the saved log
before sending it for review (redact secret tokens and any sensitive URL data).
Also verify firewall rules are present immediately after a **staging VPS/Docker
daemon reboot** using `sudo bash scripts/crawl4ai_firewall.sh check`; arrange
boot-time rule restoration if necessary. Passing the script on Docker Desktop
or before a real reboot does not certify that target-host persistence gate.

For a repeatable record of this gate on the **actual Linux VPS**, use the
following two-phase check while the bot remains explicitly disabled. Save the
report from the first command across an operator-managed host reboot:

```bash
sudo python3 scripts/check_crawl4ai_vps_reboot.py before \
  2>&1 | tee ./crawl4ai-vps-reboot-before.txt
# Reboot the VPS through the operator's normal maintenance procedure.
# Once the host and Docker services are running again:
sudo python3 scripts/check_crawl4ai_vps_reboot.py after \
  2>&1 | tee ./crawl4ai-vps-reboot-after.txt
```

The `before` phase checks the firewall and runs non-disruptive socket, Squid
and Chromium probes. The `after` phase verifies the boot ID and runs the
read-only VPS security report. When `restart=no` has kept all three Crawl4AI
containers stopped, the result is `REBOOT AFTER PASS (SAFE_DORMANT)` after
checking the disabled bot, boot service, security configuration and restart
policies. It does **not** certify live egress after the reboot. When all three
containers are running, the result must instead be `LIVE_EGRESS`; partial
startup fails. An already-recorded preflight may complete after a
verifier-only update if Git proves no runtime or security configuration files
changed in between. Both Git revisions are saved.

The report is stored with mode `0600` at
`/var/tmp/fcai-crawl4ai-reboot-state.json` by default, recording Git revisions
and boot IDs without tokens. `after` rejects an unchanged boot ID, unexpected
code changes or a failing security report. It does not restore firewall rules
or enable Crawl4AI. On failure, keep `CRAWL4AI_ENABLED=0` and inspect the boot
service; do not start dormant Crawl4AI containers to make probes pass. A
`SAFE_DORMANT` result establishes safe non-startup across a real reboot, not
firewall coverage for a running crawler during Docker startup. Controlled
public DNS/redirect/subresource fixtures are still required before activation.

The check rejects extra Docker network attachments on the crawler, Squid and
CoreDNS containers: even one additional public interface would allow traffic
outside the intended bridges. On an isolated staging engine, verify this
negative case by temporarily attaching a disposable extra bridge to the
crawler, confirming `crawl4ai_firewall.sh check` fails, and disconnecting it
before rechecking. Never add an extra network to a production crawler for a test.
On Docker Desktop, use the disposable Docker-in-Docker regression helper for
that negative test and send its generated `%TEMP%` report for review:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_crawl4ai_dind_networks.ps1
```

For the additional 20-public-site **Docker Desktop** smoke (which does not
replace the VPS staging gate), run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check_crawl4ai_public_sites.ps1
```

The helper creates `%TEMP%\fcai-public-smoke-*.txt`, and exits nonzero if a
required fixture, any URL or the before/after firewall check fails. Review
the resulting report before sharing it.

On public fixture servers **controlled by this project**, configure redirects, HTML image/iframe requests and DNS rebinding to the private test sink. Run:

The runner requires an explicit URL/redirect denial for redirect and DNS tests.
A generic `crawl_failed` is **inconclusive** (for example, an anti-bot or
fixture failure) and cannot establish SSRF protection. Confirm the controlled
fixture actually exercises the redirect/rebinding path, inspect independent
sink and DNS logs, and require zero unauthorized sink requests.

```bash
python scripts/test_crawl4ai_isolation.py \
  --public-url 'https://controlled.example.test/' \
  --redirect-url 'https://controlled.example.test/redirect?to={sink_url}' \
  --subresource-url 'https://controlled.example.test/subresources?src={sink_url}' \
  --rebind-url 'http://controlled-rebind.example.test/'
```

Substitute real controlled hostnames. Verify **zero sink HTTP requests** in direct-private, redirect, rebinding, image/iframe and proxy bypass cases. Also test loopback, metadata address, proxy outage and crawler timeout, with generic-reader fallback. Complete a sample of **at least 20 representative URLs**; record extraction, fallback, latency and RAM/CPU. Confirm the fixed REST `BrowserConfig`/`CrawlerRunConfig` proxy shape against the actual Crawl4AI **0.9.3** image. Unit tests do not prove live isolation.

Prepare a UTF-8 file with **at least 20 distinct, public, representative URLs**,
including static, JavaScript-heavy, redirects and pages loading ordinary
subresources. One URL per line; comments beginning with `#` are allowed.
Keep Crawl4AI disabled in the bot configuration while running this direct,
explicit staging probe:

```bash
docker compose cp ./staging-urls.txt bot:/tmp/crawl4ai-smoke-urls.txt
docker compose cp ./scripts/smoke_crawl4ai.py bot:/tmp/smoke_crawl4ai.py
docker compose exec -e PYTHONPATH=/app bot python /tmp/smoke_crawl4ai.py --urls-file /tmp/crawl4ai-smoke-urls.txt
```

The production image copies `app/` and `config/`, not `scripts/`, so both
the manifest and the smoke runner must be copied into the running bot
container. Only perform this explicit staging check with the trusted private
Crawl4AI and Squid containers already running; it does not toggle the bot's
`CRAWL4AI_ENABLED` setting.

The probe checks the private endpoint and proxy configuration, rejects
malformed/private URLs, requires nonempty Markdown from each result, rejects
forbidden returned destinations, and prints success counts and p50/p95 times.
Any URL failure exits nonzero. A 20-URL success is still insufficient unless
the dedicated SSRF sink and firewall checks also pass.

Only after all security and smoke gates pass, set `CRAWL4AI_ENABLED=1` and recreate the bot:

```bash
docker compose up -d --no-deps --force-recreate bot
docker compose cp ./scripts/smoke_crawl4ai.py bot:/tmp/smoke_crawl4ai.py
docker compose exec -e PYTHONPATH=/app bot python /tmp/smoke_crawl4ai.py
```

`crawl4ai` has no hard `depends_on` relationship from the bot. The bot remains functional when the Crawl4AI profile is stopped or unavailable.

For an additional JS-heavy representative page:

```bash
docker compose exec -e PYTHONPATH=/app \
  -e CRAWL4AI_SMOKE_JS_URL=https://example-js-heavy-site.invalid \
  bot python /tmp/smoke_crawl4ai.py
```

Use a real public representative URL instead of the placeholder above.
Copy the runner again after any bot container recreation.

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

### Crawl4AI output under Web Evidence V2

Crawl4AI markdown is treated exactly like generic reader text: it always goes through the same bounded excerpt selection (`[SOURCE n / CHUNK m]` labels plus URL) and the same relevance/diversity synthesis packing. A Crawl4AI result that yields no usable evidence still falls back to the generic reader once, and its existing `CRAWL4AI_MAX_CHARS` ceiling (default 12000, config max 50000) is unchanged — the pipeline never raises the 120000-character internal candidate scan above the reader path nor the 12000-character synthesis cap. There is no raw-markdown fallback switch; `CRAWL4AI_ENABLED=0` disables the backend entirely.

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

The standard audit CI runs Crawl4AI-specific tests, the regression suite,
Ruff, dependency, compile and Compose checks. The separate `Crawl4AI isolated
SSRF` workflow uses a disposable Docker-in-Docker engine on GitHub Actions to
exercise real `/crawl` requests against public/private fixtures, redirect,
image/iframe loads, DNS rebinding, firewall rules and safe-dormant daemon
restart. It refuses to run outside its explicitly flagged CI container and
never uses production credentials. Its DNS fixture replaces CoreDNS inside
that disposable engine; static CoreDNS config checks run in the audit suite.

CI **cannot** prove that firewall rules cover the Docker startup interval on
the actual VPS, or that its rules survive a physical host reboot. These are
still production activation gates. Keep `CRAWL4AI_ENABLED=0` until separate
target-host evidence and explicit approval are available.

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
