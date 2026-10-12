# Crawl4AI DNS egress and staging verification

## Why a resolver is required

Docker's embedded DNS on an `internal: true` network cannot resolve public
hostnames without a reachable external resolver. Crawl4AI 0.9.3's bundled
DNS-pinning broker resolves each target before sending it through Squid, so
DNS failures cause the real-site smoke test to fail even when static-IP
fixtures work.

The `crawl4ai-dns` service runs pinned CoreDNS on a separate **internal**
bridge, with an address controlled by `CRAWL4AI_DNS_IP` (default
`10.221.57.53` within `CRAWL4AI_DNS_SUBNET`, default `10.221.57.0/24`).
CoreDNS forwards queries to Docker's local `127.0.0.11` stub, from its own
uplink-attached namespace. It returns an empty response for AAAA queries
because the isolated crawler and Squid uplink have no IPv6 routing. This is
an IPv4-only deployment; IPv6-only websites require generic-reader fallback.

The crawler joins the private DNS bridge, **not** the public uplink. A host
firewall must permit only UDP/TCP 53 from the crawler's DNS interface to
CoreDNS, replies on established flows, and its original TCP 3128 connection
to Squid. The firewall denies all raw uplink traffic from the DNS container,
direct crawler traffic, access to the Docker host and every other container
connection. CoreDNS is read-only, runs as the image's nonroot account and has
only `NET_BIND_SERVICE` (required by its executable). Neither DNS nor Squid
publishes a host port.

DNS alone is not an SSRF boundary. The image's egress broker validates and
pins public destinations, Squid rejects non-public destinations at connect
time, and the host firewall is the final network deny layer. All three must
be verified **before** `CRAWL4AI_ENABLED=1` on production.

## Required checks on Linux staging / the actual VPS

1. Keep `CRAWL4AI_ENABLED=0`. If the chosen DNS subnet conflicts with an
   existing route, configure **both** `CRAWL4AI_DNS_SUBNET` and
   `CRAWL4AI_DNS_IP` to a non-conflicting private subnet/address.
2. Run `docker compose --profile crawl4ai config --no-env-resolution --quiet`.
   Start the services only on authorized staging infrastructure:
   `docker compose --profile crawl4ai up -d`.
3. Apply `sudo bash scripts/crawl4ai_firewall.sh apply`, then run `check`.
   Confirm Docker's actual firewall backend (iptables or nftables), three
   internal bridges, disabled IPv6, and current container IPs.
4. Resolve a known public hostname inside the crawler. Confirm that IPv4
   answers are returned and an HTTPS page crawls through the pinned Squid
   proxy. Check Squid access logs for CONNECT to a public IPv4 address.
5. Run the controlled integration SSRF suite against a project-owned public
   fixture and a private sink. Check **the sink's request count**, not just
   the crawler response: block private and metadata IPs, redirects to private,
   DNS rebinding, image/iframe subresources and direct proxy bypass.
   Repeat with CoreDNS down and with Squid down; the bot must use its generic
   reader and the private sink must receive no requests.
6. Restart CoreDNS, Crawl4AI and Squid, verify `check` still passes, and
   re-run the SSRF suite. A stale or absent rule means **do not enable**.
7. Run `python scripts/smoke_crawl4ai.py --urls-file scripts/crawl4ai_smoke_urls.txt`
   with staging `CRAWL4AI_URL`, `CRAWL4AI_API_TOKEN`, and
   `CRAWL4AI_EGRESS_PROXY_URL` configured. Record the passing URLs and any
   per-site anti-bot failures. Do not count blocked sites as successes.
8. Repeat host firewall checks on the actual VPS, including the nftables
   backend if Docker uses nftables. Only then decide whether to enable.

## Local Docker-in-Docker observations (2026-10-12)

On isolated Linux Docker-in-Docker in Docker Desktop, the 0.9.3 crawler
originally failed to resolve `example.com` with Docker's internal DNS.
Adding CoreDNS 1.12.4, forwarding to the Docker stub and suppressing AAAA
queries resolved the hostname to public IPv4 under the **applied** iptables
policy. `crawl4ai_firewall.sh apply` and `check` passed; direct socket probes
to the private sink, metadata IP, public `1.1.1.1:443`, DNS service port 80
and Docker bridge gateway were blocked. The isolated private-sink log still
showed only the earlier positive control request after the URL, redirect and
subresource probes: the private sink received zero additional requests.

The first real-site set passed 17/20 URLs. `https://www.w3.org/` presented a
Cloudflare challenge, `https://www.gnu.org/` produced a crawler failure, and
`https://ubuntu.com/` timed out. These were recorded as failures, not counted
as successes. The revised **20 distinct public URL** manifest in
`scripts/crawl4ai_smoke_urls.txt` passed **20/20** in the isolated Docker
environment on 2026-10-12: p50 0.910 s, p95 2.164 s, min 0.456 s, max
2.411 s. This measures successful page extraction, not permission to enable
production.

The cached `https://example.com/` page remained available from the crawler
while Squid was stopped. A fresh-URL outage probe was blocked by the command
approval system before it executed. A definitive proxy-down fallback test,
controlled DNS-rebinding fixture, nftables if used on the VPS, and the actual
VPS firewall check remain mandatory. Keep `CRAWL4AI_ENABLED=0` until those
staging checks pass. These observations do not validate the host firewall of
the production VPS.

## Rollback

Set `CRAWL4AI_ENABLED=0` and restart the bot. Keep firewall deny rules
active. Reverting the DNS change must revert the DNS Compose service,
`crawl4ai/Corefile`, and the associated firewall rules together. Re-apply
and check the relevant security policy after recreating containers; never
enable the crawler if the firewall check fails.
