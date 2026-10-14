#!/usr/bin/env bash
# One read-only command for the operator: sudo bash scripts/verify_crawl4ai_security.sh
set -euo pipefail
here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec bash "$here/check_crawl4ai_report.sh" "$@"
