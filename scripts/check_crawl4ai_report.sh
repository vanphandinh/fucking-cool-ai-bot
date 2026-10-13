#!/usr/bin/env bash
# One-command, non-disruptive Crawl4AI security diagnostics for a Linux VPS.
# Run: sudo bash scripts/verify_crawl4ai_security.sh
# No Docker restarts, firewall changes, VPS reboot, or Crawl4AI enablement.
set -uo pipefail
umask 077

if [ "${1:-}" = --help ]; then
  echo 'Usage: sudo bash scripts/check_crawl4ai_report.sh'
  echo 'Writes a private diagnostic report to /var/tmp and prints its location.'
  exit 0
fi
if [ "$#" -ne 0 ]; then
  echo 'Usage: sudo bash scripts/check_crawl4ai_report.sh' >&2
  exit 2
fi
if [ "$(uname -s)" != Linux ]; then
  echo 'This diagnostic must run on a Linux VPS.' >&2
  exit 2
fi

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)" || exit 2
report="$(mktemp /var/tmp/fcai-crawl4ai-report-XXXXXXXX.txt)" || {
  echo 'Cannot create private report in /var/tmp' >&2
  exit 2
}

check() {
  local label="$1" output rc
  shift
  output="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf 'PASS: %s\n' "$label"
  else
    printf 'FAIL: %s (exit %s)\n' "$label" "$rc"
  fi
  # Child stderr/stdout may contain URL or environment data. Only include
  # deliberately formatted PASS markers from the live probes in the report.
  if [ "$rc" -eq 0 ] && [ "$label" = 'Live network isolation (sockets, Squid, Chromium)' ]; then
    printf '%s\n' "$output" | grep -E '^(PASS |LIVE EGRESS PASS)' || true
  elif [ "$label" = 'Static config and boot evidence' ]; then
    printf '%s\n' "$output" | grep -E '^(PASS|FAIL|UNVERIFIED): ' || true
  fi
  return "$rc"
}

check_env_off() {
  python3 - "$root/.env" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
if not path.is_file():
    sys.exit('Missing .env')
content = path.read_text(encoding='utf-8-sig')
values = re.findall(r'^\s*CRAWL4AI_ENABLED\s*=\s*([^#\r\n]*)', content, re.M)
if not values or values[-1].strip().strip('"\'').lower() not in ('0', 'false', 'off', 'no'):
    sys.exit('CRAWL4AI_ENABLED must explicitly equal 0 (or false/off/no)')
print('CRAWL4AI_ENABLED=0 (verified without displaying .env)')
PY
}

diagnose() {
  local docker_os container state
  printf 'Crawl4AI VPS security diagnostics (read-only)\n'
  printf 'UTC: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'Host: %s\n' "$(hostname)"
  printf 'Kernel: %s\n' "$(uname -sr)"
  printf 'Repository: %s\n' "$root"
  git -C "$root" log -1 --format='Git commit: %H %s' 2>/dev/null || true
  printf '\n'

  if [ "$(uname -s)" != Linux ] || [ "$(id -u)" -ne 0 ]; then
    echo 'INCOMPLETE: run as root on an actual Linux VPS.'
    return 1
  fi
  if ! command -v docker >/dev/null || ! command -v python3 >/dev/null; then
    echo 'INCOMPLETE: Docker and Python 3 must be installed.'
    return 1
  fi
  docker_os="$(docker info --format '{{.OperatingSystem}}' 2>&1)" || {
    echo 'INCOMPLETE: Docker daemon unavailable.'
    return 1
  }
  case "${docker_os,,}" in
    *docker\ desktop*)
      echo 'INCOMPLETE: Docker Desktop cannot verify Linux VPS isolation.'
      return 1 ;;
  esac
  printf 'Docker host: %s\n\n' "$docker_os"

  if ! check 'Static config and boot evidence' \
    python3 "$root/scripts/crawl4ai_security_audit.py"; then
    echo 'STOP: security configuration or boot service check failed.'
    return 1
  fi
  printf '\n'

  if ! check 'Crawl4AI disabled in .env' check_env_off; then
    echo 'STOP: Crawl4AI is not confirmed disabled; network probes skipped.'
    return 1
  fi
  if ! check 'Running bot has Crawl4AI disabled' docker exec fcai-bot python -c \
    'from app.config import Settings; import sys; sys.exit(0 if not Settings(_env_file=None).crawl4ai_enabled else 1)'; then
    echo 'STOP: running bot not confirmed disabled; network probes skipped.'
    return 1
  fi

  printf '\nContainer status:\n'
  for container in fcai-bot fcai-crawl4ai fcai-crawl4ai-proxy fcai-crawl4ai-dns; do
    state="$(docker inspect --format '{{.State.Status}}' "$container" 2>/dev/null)" || state=MISSING
    printf '  %s: %s\n' "$container" "$state"
  done
  printf '\n'

  if ! check 'Firewall and network attachments' bash "$root/scripts/crawl4ai_firewall.sh" check; then
    echo 'STOP: firewall gate failed; live network probes skipped.'
    return 1
  fi
  printf '\n'
  if ! check 'Live network isolation (sockets, Squid, Chromium)' \
    python3 "$root/scripts/check_crawl4ai_vps_live.py" --live; then
    echo 'INCOMPLETE: live network checks need investigation.'
    return 1
  fi

  printf '\nRESULT: PASS for live VPS egress isolation; remaining items UNVERIFIED.\n'
  echo 'Controlled public SSRF fixtures and startup-interval tests require isolated staging.'
  echo 'CRAWL4AI_ENABLED must remain 0 in production.'
  return 0
}

diagnose 2>&1 | tee "$report"
pipeline_rc=("${PIPESTATUS[@]}")
rc="${pipeline_rc[0]}"
if [ "${pipeline_rc[1]}" -ne 0 ]; then
  echo 'Report could not be written completely.' >&2
  exit 2
fi
printf '\nREPORT FILE: %s\n' "$report"
echo 'Send this report file for review; do not send .env or tokens.'
exit "$rc"
