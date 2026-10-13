"""Opt-in, non-disruptive Crawl4AI VPS egress probes.

Run with ``sudo python3 scripts/check_crawl4ai_vps_live.py --live``.
This does not restart containers, change firewall rules or enable the bot.
Controlled redirect, DNS rebinding and private-sink tests remain mandatory on
dedicated staging, as does the VPS reboot-persistence check.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

DIRECT_EGRESS = """
import socket
import sys

for label, address, port in (
    ('public egress', '1.1.1.1', 80),
    ('metadata egress', '169.254.169.254', 80),
    ('private egress', '10.0.0.1', 80),
):
    try:
        with socket.create_connection((address, port), timeout=3):
            print('FAIL direct ' + label + ': connection established')
            sys.exit(1)
    except OSError:
        print('PASS direct ' + label + ': no connection')
"""

PROXY_EGRESS = """
import http.client
import sys

def status_for(url, host):
    client = http.client.HTTPConnection('crawl4ai-proxy', 3128, timeout=8)
    try:
        client.request('GET', url, headers={'Host': host, 'Connection': 'close'})
        return client.getresponse().status
    finally:
        client.close()

public_status = status_for('http://example.com/', 'example.com')
if not 200 <= public_status < 400:
    sys.exit('FAIL public request through Squid: HTTP ' + str(public_status))
print('PASS public request through Squid: HTTP ' + str(public_status))

for name, address in (
    ('metadata', '169.254.169.254'),
    ('private', '10.0.0.1'),
    ('loopback', '127.0.0.1'),
):
    status = status_for('http://' + address + '/', address)
    if status != 403:
        sys.exit('FAIL Squid ' + name + ' address: HTTP ' + str(status))
    print('PASS Squid ' + name + ' address: HTTP 403')
"""

CHROMIUM_EGRESS = """
import asyncio
import sys
from playwright.async_api import async_playwright, Error, TimeoutError as BrowserTimeout

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--no-sandbox'])
        try:
            page = await browser.new_page()
            try:
                response = await page.goto('https://example.com/', timeout=7000,
                                           wait_until='domcontentloaded')
            except Error as exc:
                if isinstance(exc, BrowserTimeout) or any(term in str(exc) for term in (
                    'net::ERR_ADDRESS_UNREACHABLE', 'net::ERR_TIMED_OUT',
                    'net::ERR_CONNECTION_TIMED_OUT', 'net::ERR_CONNECTION_REFUSED',
                )):
                    print('PASS unproxied Chromium: public egress blocked')
                    return 0
                print('INCONCLUSIVE Chromium: ' + type(exc).__name__, file=sys.stderr)
                return 2
            print('FAIL unproxied Chromium: received HTTP ' +
                  str(response.status if response else 'unknown'), file=sys.stderr)
            return 1
        finally:
            await browser.close()

sys.exit(asyncio.run(main()))
"""


def run(*argv: str, timeout: int = 90) -> str:
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                            timeout=timeout, check=False)
    if result.returncode:
        # Preserve diagnostics but avoid printing environment variables/tokens.
        message = (result.stdout + result.stderr).strip()
        raise RuntimeError(f"{argv[0]} check failed (exit {result.returncode}): {message}")
    return result.stdout.strip()


def require_disabled() -> None:
    env_file = ROOT / ".env"
    content = env_file.read_text(encoding="utf-8-sig")
    declarations = re.findall(r"^\s*CRAWL4AI_ENABLED\s*=\s*([^#\r\n]*)",
                              content, flags=re.MULTILINE)
    if not declarations or declarations[-1].strip().strip("\"'").lower() not in (
        "0", "false", "off", "no",
    ):
        raise RuntimeError(".env must explicitly disable Crawl4AI")
    run("docker", "exec", "fcai-bot", "python", "-c",
        "from app.config import Settings; import sys; "
        "sys.exit(0 if not Settings(_env_file=None).crawl4ai_enabled else 1)")
    print("PASS bot Crawl4AI setting remains disabled")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Run opt-in VPS network probes")
    args = parser.parse_args()
    if not args.live or sys.platform != "linux" or os.geteuid() != 0:
        parser.error("Linux VPS, root and explicit --live are required")
    try:
        docker_os = run("docker", "info", "--format", "{{.OperatingSystem}}")
        if "docker desktop" in docker_os.lower():
            raise RuntimeError("Docker Desktop cannot certify the VPS firewall")
        require_disabled()
        firewall = str(ROOT / "scripts" / "crawl4ai_firewall.sh")
        run("bash", firewall, "check")
        print("PASS VPS firewall preflight")
        for label, source, timeout in (
            ("socket", DIRECT_EGRESS, 30),
            ("Squid", PROXY_EGRESS, 60),
            ("Chromium", CHROMIUM_EGRESS, 45),
        ):
            output = run("docker", "exec", "fcai-crawl4ai", "python", "-c", source,
                         timeout=timeout)
            print(output)
            print(f"PASS {label} probe completed")
        run("bash", firewall, "check")
        print("PASS VPS firewall postflight")
        print("LIVE EGRESS PASS: still requires controlled SSRF sink/DNS and reboot tests")
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"LIVE EGRESS INCOMPLETE: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
