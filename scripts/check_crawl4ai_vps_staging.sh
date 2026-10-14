#!/usr/bin/env bash
# Run ONLY on a dedicated Linux staging VPS with controlled public SSRF fixtures.
# Never use against a production Docker engine. The bot stays disabled.
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
usage: sudo bash scripts/check_crawl4ai_vps_staging.sh --staging \
  --public-url URL --redirect-url URL --subresource-url URL \
  --rebind-url URL --urls-file FILE --restart --recreate

All fixture URLs must belong to project-controlled public test servers.
--restart  Mandatory: verify firewall after restarting crawler, proxy and DNS.
--recreate Mandatory: verify/reapply firewall after recreating those services.
Neither flag changes the bot or enables Crawl4AI for its running process.
USAGE
  exit 2
}

staging=0
restart=0
recreate=0
public_url=
redirect_url=
subresource_url=
rebind_url=
urls_file=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --staging) staging=1; shift ;;
    --restart) restart=1; shift ;;
    --recreate) recreate=1; shift ;;
    --public-url|--redirect-url|--subresource-url|--rebind-url|--urls-file)
      [ "$#" -ge 2 ] || usage
      case "$1" in
        --public-url) public_url="$2" ;;
        --redirect-url) redirect_url="$2" ;;
        --subresource-url) subresource_url="$2" ;;
        --rebind-url) rebind_url="$2" ;;
        --urls-file) urls_file="$2" ;;
      esac
      shift 2 ;;
    *) usage ;;
  esac
done

if [ "$staging" -ne 1 ] || [ "$(uname -s)" != Linux ] || [ "$(id -u)" -ne 0 ]; then
  echo 'Dedicated Linux staging VPS, explicit --staging and root are required' >&2
  exit 2
fi
if [ -z "$public_url" ] || [ -z "$redirect_url" ] ||
   [ -z "$subresource_url" ] || [ -z "$rebind_url" ] || [ ! -s "$urls_file" ] ||
   [ "$restart" -ne 1 ] || [ "$recreate" -ne 1 ]; then
  usage
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
command -v docker >/dev/null
command -v python3 >/dev/null
docker_os="$(docker info --format '{{.OperatingSystem}}')"
case "${docker_os,,}" in
  *docker\ desktop*)
    echo 'Docker Desktop is not a valid Linux VPS staging firewall gate' >&2
    exit 2 ;;
esac
docker compose --profile crawl4ai config --quiet

# Refuse an active crawler in both the Compose .env file and running bot.
# The one-shot fallback probe overrides the flag only in its own child process.
if [ ! -f .env ] || ! python3 - <<'PY'
from pathlib import Path
import re
from sys import exit
text = Path('.env').read_text(encoding='utf-8-sig')
values = re.findall(r'^\s*CRAWL4AI_ENABLED\s*=\s*([^#\r\n]*)', text, re.M)
exit(0 if values and values[-1].strip().strip('"\'').lower() in ('0', 'false', 'off', 'no') else 1)
PY
then
  echo 'Crawl4AI must be explicitly disabled in the staging .env file' >&2
  exit 2
fi
if ! docker exec fcai-bot python -c \
  'from app.config import Settings; import sys; sys.exit(int(Settings(_env_file=None).crawl4ai_enabled))'; then
  echo 'Running bot must have CRAWL4AI_ENABLED=0' >&2
  exit 2
fi

firewall() { bash scripts/crawl4ai_firewall.sh check; }
wait_healthy() {
  local name="$1" state attempt
  for attempt in $(seq 1 30); do
    state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name")"
    if [ "$state" = healthy ] || [ "$state" = running ]; then return 0; fi
    sleep 2
  done
  echo "Container failed to become healthy: $name ($state)" >&2
  return 1
}

echo '=== Staging identity (no secrets) ==='
uname -sr
docker info --format 'Docker server={{.OperatingSystem}} firewall_backend={{.FirewallBackend}}'
echo '=== Initial firewall gate ==='
firewall

if [ "$restart" -eq 1 ]; then
  echo '=== Restart persistence gate ==='
  docker compose --profile crawl4ai restart crawl4ai crawl4ai-proxy crawl4ai-dns
  wait_healthy fcai-crawl4ai
  wait_healthy fcai-crawl4ai-proxy
  wait_healthy fcai-crawl4ai-dns
  firewall
fi

if [ "$recreate" -eq 1 ]; then
  echo '=== Recreate / drift / reapply gate (staging only) ==='
  docker compose --profile crawl4ai up -d --no-deps --force-recreate \
    crawl4ai crawl4ai-proxy crawl4ai-dns
  # Recreation may change container IPs. Fail closed until policy is re-applied.
  if firewall; then
    echo 'Prior firewall bindings remained valid after recreation'
  else
    echo 'Firewall drift detected; reapplying before testing network access'
  fi
  bash scripts/crawl4ai_firewall.sh apply
  firewall
  wait_healthy fcai-crawl4ai
  wait_healthy fcai-crawl4ai-proxy
  wait_healthy fcai-crawl4ai-dns
fi

echo '=== Controlled SSRF sink, redirect, DNS and browser isolation ==='
python3 scripts/test_crawl4ai_isolation.py \
  --public-url "$public_url" \
  --redirect-url "$redirect_url" \
  --subresource-url "$subresource_url" \
  --rebind-url "$rebind_url"
firewall

echo '=== 20+ representative public URL smoke ==='
docker cp "$urls_file" fcai-bot:/tmp/fcai-staging-urls.txt
docker cp scripts/smoke_crawl4ai.py fcai-bot:/tmp/fcai-staging-smoke.py
docker exec -e PYTHONPATH=/app -e CRAWL4AI_EGRESS_PROXY_URL=http://crawl4ai-proxy:3128 \
  fcai-bot python /tmp/fcai-staging-smoke.py --urls-file /tmp/fcai-staging-urls.txt
firewall

echo '=== Squid outage must fall back to the generic reader ==='
proxy_stopped=0
restore_proxy() {
  if [ "$proxy_stopped" -eq 1 ]; then
    echo 'Restoring Squid after fault injection'
    if ! docker compose --profile crawl4ai start crawl4ai-proxy; then
      echo 'WARNING: Squid restoration failed; inspect the staging proxy' >&2
    fi
  fi
}
trap restore_proxy EXIT
proxy_stopped=1
docker compose --profile crawl4ai stop crawl4ai-proxy
docker exec -e CRAWL4AI_ENABLED=1 \
  -e CRAWL4AI_EGRESS_PROXY_URL=http://crawl4ai-proxy:3128 \
  fcai-bot python -c '
import asyncio
import sys
from app.config import Settings
from app.search.url_service import read_url
settings = Settings(_env_file=None)
if not settings.crawl4ai_ready:
    sys.exit("Crawl4AI was not properly configured for the isolated fallback probe")
result = asyncio.run(read_url(sys.argv[1], settings))
print("generic fallback ok=%s backend=%s" % (result.ok, result.backend))
sys.exit(0 if result.ok and result.backend == "generic_reader" else 1)
' "$public_url"
docker compose --profile crawl4ai start crawl4ai-proxy
proxy_stopped=0
trap - EXIT
wait_healthy fcai-crawl4ai-proxy
firewall

echo '=== Container resource snapshot ==='
docker stats --no-stream --format '{{.Name}} CPU={{.CPUPerc}} MEM={{.MemUsage}}' \
  fcai-bot fcai-crawl4ai fcai-crawl4ai-proxy fcai-crawl4ai-dns
echo 'STAGING PROBES PASS: firewall/restart/recreate, controlled sink, 20+ URLs, proxy outage'
echo 'Operator must still review DNS fixture evidence, firewall boot persistence and results before production activation.'
