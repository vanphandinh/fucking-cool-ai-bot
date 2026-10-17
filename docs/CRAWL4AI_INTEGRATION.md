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
On the VPS, run one read-only diagnostic command from the repository root:

```bash
sudo bash scripts/verify_crawl4ai_security.sh
```

This produces a private report with `PASS`, `FAIL` and `UNVERIFIED` items.
The command does not restart containers, modify firewall rules or reboot the VPS.
To eliminate the observed boot-time window, the three Crawl4AI containers now
use `restart: "no"`. After a host reboot they remain **stopped** by design;
the report then says `SAFE DORMANT` and does not claim live egress was tested.
The bot continues normally with `CRAWL4AI_ENABLED=0`. Previously created VPS
containers retain their old restart policies until updated explicitly. After
pulling the new commit onto the VPS, run **one command** to update their
policies and collect the read-only security report:

```bash
sudo bash scripts/apply_crawl4ai_safe_boot.sh
```

This opt-in command changes only the existing three Docker restart policies;
it refuses partial startup and fails if the running bot has Crawl4AI enabled.
It neither restarts containers, alters firewall rules, nor reboots the host.
It then runs `verify_crawl4ai_security.sh` and prints the private report path.
The original `verify_crawl4ai_security.sh` remains read-only.
Never enable Crawl4AI just because the boot window is removed: manual container
startup still requires firewall reapplication and independent SSRF evidence.
`UNVERIFIED` items (controlled SSRF fixtures, Docker startup interval) still
require separate controlled evidence; they are not authorization to enable
Crawl4AI. The older `check_crawl4ai_report.sh` entry point remains supported.
Run disruptive restart/recreate fault tests only in a disposable Docker engine.

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

### Extended isolated smoke checks (2026-10-12)

A disposable test fixture (`scripts/crawl4ai_varied_fixture.py`) supplied
**20 distinct routes** on the isolated Docker-in-Docker uplink: 12 static HTML,
four pages with rendered JavaScript, two pages loading public image/iframe
resources, and two public redirects. The pinned REST `/crawl` API successfully
extracted the required marker from **20/20** pages (p50 **0.65 s**, p95
**0.75 s**, wall-clock per probe). A separate crawl confirmed that the
JavaScript-injected `FCAI JS RENDERED` text appeared in Markdown. Server logs
recorded actual `GET /image.png` and `GET /frame` requests. Crawl4AI logs
confirmed repeated reuse of the same **hot pool** browser.

After reuse, a controlled redirect and a page with private image/iframe URLs
were rejected by `/crawl` (`success=False`). The private request sink log had
**no additional requests**, and `crawl4ai_firewall.sh check` continued to pass
with iptables. At the resource snapshot, the crawler used **505.5 MiB RAM**.
The disposable bot test container had to be restarted after its scheduled
`sleep 3600` command ended; the firewall check failed closed while that bot
was absent and passed again once it was restarted.

The 20 routes exercise distinct local page behaviors, **not 20 independent
representative websites**. They are not a substitute for the full staging
acceptance test against diverse public sites. The isolated host uses the
iptables backend; nftables remains unverified on the intended VPS.

A further end-to-end run built the current production `Dockerfile` into an
isolated, disposable bot image, then executed the actual
`scripts/smoke_crawl4ai.py --urls-file` runner from the running bot network
namespace (with `CRAWL4AI_ENABLED=0`). All **20/20 controlled URLs passed**:
p50 **0.339 s**, p95 **0.398 s**, maximum **0.973 s**. The image's
`python -m pip check` reported `No broken requirements found`. The Windows
host's unrelated Python 3.13 install has a `virtualenv`/`filelock` conflict;
its global `pip check` result is not an image dependency failure.

**2026-10-13 independent public-site smoke (Docker Desktop):** the manually
run `scripts/check_crawl4ai_public_sites.ps1` invoked the real Crawl4AI 0.9.3
`/crawl` endpoint from an isolated bot-image container in the disposable
Docker-in-Docker network. All **20/20 distinct public websites passed**,
including Python documentation, SQLite, Rust, Go, PostgreSQL, Debian, Docker
documentation, MDN, Wikipedia, Cloudflare and W3Schools. Every result returned
nonempty Markdown; `smoke_crawl4ai.py` reported p50 **1.115 s**, p95
**2.475 s**, minimum **0.586 s** and maximum **8.902 s**. The Linux
`crawl4ai_firewall.sh check` reported **PASS (iptables)** immediately before
and after the crawl. The generated report is
`fcai-public-smoke-20261013-004525-005.txt` (user's Windows TEMP directory).
The test did **not** change the bot's `CRAWL4AI_ENABLED=0` setting or touch
production. It demonstrates public-site rendering on Docker Desktop, **not**
firewall persistence or SSRF isolation on the target Linux VPS; the staging
gate still requires its own run of these 20 representative URLs.

An additional **live generic-reader failover** check ran `read_url()` from
the same disposable bot image with `CRAWL4AI_ENABLED=1` supplied to the test
process only. The isolated bot network had independent, ordinary outbound
access; Squid was stopped in the disposable engine. Crawl4AI returned
`http_400`, after which `read_url("https://example.com")` returned
`backend=generic_reader`, `ok=True`, with 171 characters of real fetched
content. The earlier baseline with Crawl4AI disabled also read the page
successfully. Squid was restarted, a controlled fixture crawl succeeded
again, and the iptables firewall check passed. The private sink's log had
no new HTTP request. This confirms live fallback in the Docker Desktop
test topology; staging networking and latency still require verification.

The independent **Docker nftables backend** was also exercised in a second,
disposable Docker-in-Docker engine (Docker 29.9.0, nftables 1.1.6). Three
project-named bridge networks and small Python HTTP fixture containers
represented the bot, crawler and proxy; the actual Crawl4AI image and Squid
were **not** launched in this nftables-only check. The firewall script's
`apply` and `check` commands both passed. The bot reached the crawler's
port 11235, the crawler reached the proxy's port 3128, and the proxy loaded
`https://example.com` over public HTTPS. Attempts from the crawler to the
bot (11235) and proxy (80), and from the proxy to the bot's private address
or the metadata address 169.254.169.254, timed out. The bot fixture's access
log remained empty. Direct crawler access to public IP 1.1.1.1:80 returned
`Network unreachable`. Following a crawler-container restart, `check` passed
and the allowed proxy connection still worked. This establishes that the
generated nftables rules can be applied and enforce these selected packet
paths in Docker Desktop; a real Crawl4AI/Squid run and the chosen VPS
firewall backend must still pass the full isolated staging suite.

For a disposable *existing* Docker-in-Docker test network, create the varied
fixture with `--host 0.0.0.0` inside its own container. The fixture binds only
to loopback by default when started outside Docker. Generate its manifest with:

```bash
python scripts/crawl4ai_varied_fixture.py --manifest-base http://11.23.0.51
```

`11.23.0.51` is an **isolated test-network address only**; never direct a
production crawler or a test run on the public Internet to that address.

**Still required:** repeat DNS rebinding and positive/negative subresource
instrumentation on the target staging host, validate the actual VPS firewall
backend (including nftables if selected), repeat live generic-reader
proxy-outage fallback on
staging, verify pool reuse on the staging host, and run the representative
20-URL smoke test against real sites **on staging**. The Docker Desktop
20/20 public-site smoke, hot-pool reuse and live fallback checks are complete;
these do not prove the permanent
browser's egress posture or availability on staging.
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

Leave `CRAWL4AI_ENABLED=0` on the initial deploy. On a dedicated **Linux staging VPS** with Docker Compose and root firewall access (the following startup sequence must **not** be treated as proven safe for production):

```bash
docker compose --profile crawl4ai pull
docker compose --profile crawl4ai up -d --build
sudo bash scripts/crawl4ai_firewall.sh apply
sudo bash scripts/crawl4ai_firewall.sh check
docker compose ps
```

The firewall script selects Docker's iptables or nftables backend and checks the network attachments. Re-run `check` **after restarting/recreating containers**; configure firewall persistence in VPS boot provisioning. If backend detection, bridging, rules, or restart verification fails, do **not** enable Crawl4AI.

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
