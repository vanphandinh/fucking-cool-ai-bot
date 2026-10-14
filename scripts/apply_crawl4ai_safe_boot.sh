#!/usr/bin/env bash
# One-time VPS preparation: disable automatic Crawl4AI container restarts,
# then run the existing read-only diagnostic. Does not restart services.
set -euo pipefail
umask 077

if [ "${1:-}" = --help ]; then
  echo 'Usage: sudo bash scripts/apply_crawl4ai_safe_boot.sh'
  echo 'Sets restart=no on the three existing Crawl4AI containers and prints a security report.'
  exit 0
fi
if [ "$#" -ne 0 ] || [ "$(uname -s)" != Linux ] || [ "$(id -u)" -ne 0 ]; then
  echo 'Run with sudo bash scripts/apply_crawl4ai_safe_boot.sh on the Linux VPS.' >&2
  exit 2
fi

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$root"

# Abort before changing policies if the production bot can use Crawl4AI.
python3 - "$root/.env" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    sys.exit('FAIL: .env is missing')
values = re.findall(r'^\s*CRAWL4AI_ENABLED\s*=\s*([^#\r\n]*)',
                    path.read_text(encoding='utf-8-sig'), flags=re.MULTILINE)
if not values or values[-1].strip().strip('"\'').lower() not in ('0', 'false', 'off', 'no'):
    sys.exit('FAIL: CRAWL4AI_ENABLED must remain disabled')
PY
if ! docker exec fcai-bot python -c \
  'from app.config import Settings; import sys; sys.exit(int(Settings(_env_file=None).crawl4ai_enabled))'; then
  echo 'FAIL: running bot must have Crawl4AI disabled.' >&2
  exit 1
fi

docker info >/dev/null
containers=(fcai-crawl4ai fcai-crawl4ai-proxy fcai-crawl4ai-dns)
running=0
dormant=0
for container in "${containers[@]}"; do
  if ! state="$(docker inspect --format '{{.State.Status}}' "$container")"; then
    echo "FAIL: missing $container; no policies changed." >&2
    exit 1
  fi
  case "$state" in
    running) running=$((running + 1)) ;;
    exited|created) dormant=$((dormant + 1)) ;;
    *) echo "FAIL: $container is $state; no policies changed." >&2; exit 1 ;;
  esac
done

if [ "$running" -eq 3 ]; then
  if ! bash "$root/scripts/crawl4ai_firewall.sh" check >/dev/null; then
    echo 'FAIL: firewall preflight; no policies changed.' >&2
    exit 1
  fi
elif [ "$dormant" -ne 3 ]; then
  echo 'FAIL: partial Crawl4AI startup; no policies changed.' >&2
  exit 1
fi

for container in "${containers[@]}"; do
  docker update --restart=no "$container" >/dev/null
  if [ "$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$container")" != no ]; then
    echo "FAIL: restart policy not applied to $container." >&2
    exit 1
  fi
done
echo 'PASS: Crawl4AI crawler, Squid and DNS auto-restart disabled (restart=no).'

if [ "$running" -eq 3 ]; then
  if ! bash "$root/scripts/crawl4ai_firewall.sh" check >/dev/null; then
    echo 'FAIL: firewall postflight.' >&2
    exit 1
  fi
fi

echo '=== Read-only VPS security diagnostics ==='
bash "$root/scripts/verify_crawl4ai_security.sh"
