#!/usr/bin/env bash
# Opt-in boot-time firewall restoration on a single Linux VPS.
# Installation never restarts containers, changes current rules, or reboots.
set -euo pipefail

usage() {
  echo 'Usage: sudo bash scripts/install_crawl4ai_firewall_boot.sh [--help]'
  echo 'Installs/enables fcai-crawl4ai-firewall.service; no host reboot is performed.'
}

if [ "${1:-}" = --help ]; then usage; exit 0; fi
case "${1:-}" in ''|--boot) ;; *) usage >&2; exit 2 ;; esac
if [ "$#" -gt 1 ]; then usage >&2; exit 2; fi
if [ "$(uname -s)" != Linux ] || [ "$(id -u)" -ne 0 ]; then
  echo 'Run only as root on a Linux VPS.' >&2
  exit 2
fi

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
firewall="$root/scripts/crawl4ai_firewall.sh"
service=fcai-crawl4ai-firewall.service
unit="/etc/systemd/system/$service"

require_disabled() {
  python3 - "$root/.env" <<'PY' || return 1
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    sys.exit('Missing .env: refusing firewall boot setup')
content = path.read_text(encoding='utf-8-sig')
values = re.findall(r'^\s*CRAWL4AI_ENABLED\s*=\s*([^#\r\n]*)', content, re.M)
if not values or values[-1].strip().strip('"\'').lower() not in ('0', 'false', 'off', 'no'):
    sys.exit('CRAWL4AI_ENABLED must remain disabled')
PY
  docker exec fcai-bot python -c \
    'from app.config import Settings; import sys; sys.exit(0 if not Settings(_env_file=None).crawl4ai_enabled else 1)' || return 1
}

if [ "${1:-}" = --boot ]; then
  # Legacy containers may still auto-restart: restore their address-bound
  # rules. With restart=no, all three can safely remain dormant after reboot.
  for attempt in $(seq 1 40); do
    if docker info >/dev/null 2>&1 && require_disabled >/dev/null 2>&1; then
      running=0
      stopped=0
      dormant_safe=1
      for container in fcai-crawl4ai fcai-crawl4ai-proxy fcai-crawl4ai-dns; do
        state="$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)"
        policy="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$container" 2>/dev/null || true)"
        if [ "$state" = true ]; then
          running=$((running + 1))
        elif [ "$state" = false ] && [ "$policy" = no ]; then
          stopped=$((stopped + 1))
        fi
        if [ "$policy" != no ]; then
          dormant_safe=0
        fi
      done
      if [ "$running" -eq 0 ] && [ "$stopped" -eq 3 ] && [ "$dormant_safe" -eq 1 ]; then
        echo 'Crawl4AI dormant after boot (no crawler/proxy/DNS running): PASS'
        exit 0
      fi
      if [ "$running" -eq 3 ] && bash "$firewall" apply && bash "$firewall" check; then
        echo 'Crawl4AI firewall restored after boot: PASS'
        exit 0
      fi
    fi
    sleep 3
  done
  echo 'Crawl4AI firewall restoration FAILED; keep CRAWL4AI_ENABLED=0' >&2
  exit 1
fi

command -v systemctl >/dev/null
command -v docker >/dev/null
command -v python3 >/dev/null
require_disabled || { echo 'Running bot must have Crawl4AI disabled.' >&2; exit 1; }
bash "$firewall" check >/dev/null || {
  echo 'Current firewall check must pass before installing boot restoration.' >&2
  exit 1
}

# systemd's ExecStart parser does not use Bash quoting; avoid writing an unsafe
# path into the unit rather than guessing how spaces or specifiers are escaped.
case "$root" in *[[:space:]%]* )
  echo 'Repository path with whitespace or % is unsupported by the unit installer.' >&2
  exit 2 ;;
esac

temporary="$(mktemp)"
trap 'rm -f -- "$temporary"' EXIT
cat > "$temporary" <<UNIT
[Unit]
Description=Restore Crawl4AI firewall after Docker startup
Requires=docker.service
After=docker.service
PartOf=docker.service
StartLimitIntervalSec=0

[Service]
Type=oneshot
WorkingDirectory=$root
ExecStart=/bin/bash $root/scripts/install_crawl4ai_firewall_boot.sh --boot
RemainAfterExit=yes
TimeoutStartSec=180
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

if [ -e "$unit" ]; then
  if ! cmp -s "$temporary" "$unit"; then
    echo "Existing $unit differs: refusing to overwrite it." >&2
    exit 1
  fi
  echo 'Existing unit matches expected content.'
else
  install -o root -g root -m 0644 "$temporary" "$unit"
fi

systemctl daemon-reload
systemd-analyze verify "$unit"
systemctl enable "$service"
systemctl is-enabled "$service"
echo 'Boot restoration enabled. No reboot, service start, or firewall change was performed.'
echo 'Before a maintenance reboot, run check_crawl4ai_vps_reboot.py before.'
echo 'After reboot, run check_crawl4ai_vps_reboot.py after and inspect journalctl -u fcai-crawl4ai-firewall.service.'
echo 'WARNING: update existing Crawl4AI containers to restart=no to avoid automatic startup before firewall restoration.'
