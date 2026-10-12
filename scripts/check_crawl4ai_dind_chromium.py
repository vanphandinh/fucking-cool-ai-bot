"""Optional, isolated Docker Desktop Chromium egress check for manual reports.

Uses the existing fcai-security-dind Docker-in-Docker fixture only. It sends
one positive-control GET to fcai-ssrf-sink and verifies that neither direct
Chromium navigation generates additional private-sink requests.
"""

from __future__ import annotations

import json
import subprocess
import sys


CHROMIUM_PROBE = """
import asyncio
import sys
from playwright.async_api import async_playwright, Error, TimeoutError as BrowserTimeout

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=['--no-sandbox'])
        print('BROWSER_STARTED', flush=True)
        try:
            page = await browser.new_page()
            for url in sys.argv[1:]:
                try:
                    response = await page.goto(url, timeout=6000, wait_until='domcontentloaded')
                except Error as exc:
                    if isinstance(exc, BrowserTimeout) or any(marker in str(exc) for marker in (
                        'net::ERR_ADDRESS_UNREACHABLE', 'net::ERR_TIMED_OUT',
                        'net::ERR_CONNECTION_TIMED_OUT', 'net::ERR_CONNECTION_REFUSED',
                    )):
                        print('BROWSER_EGRESS_BLOCKED', flush=True)
                        continue
                    print('BROWSER_INCONCLUSIVE_ERROR', type(exc).__name__, file=sys.stderr)
                    return 2
                print(
                    'BROWSER_DIRECT_ACCESS',
                    response.status if response else 'none',
                    file=sys.stderr,
                )
                return 1
            return 0
        finally:
            await browser.close()

raise SystemExit(asyncio.run(main()))
"""


def inner_docker(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", "fcai-security-dind", "docker", *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )


def ip_on_network(container: str, network: str) -> str:
    result = inner_docker("inspect", container, "--format", "{{json .NetworkSettings.Networks}}")
    networks = json.loads(result.stdout)
    ip = networks.get(network, {}).get("IPAddress", "")
    if not ip:
        raise RuntimeError(f"missing controlled fixture network: {container}/{network}")
    return ip


def private_request_count() -> int:
    result = inner_docker("logs", "fcai-ssrf-sink")
    return (result.stdout + result.stderr).count('"GET /ping HTTP/1.1"')


def main() -> int:
    sink_ip = ip_on_network("fcai-ssrf-sink", "fcai-crawl4ai-api")
    public_ip = ip_on_network("fcai-public-fixture", "fcai-crawl4ai-uplink")
    # The fixture is a disposable HTTP server. A 404 or 2xx response is an
    # expected positive control: in both cases it must log GET /ping.
    control = """import urllib.request,urllib.error
try:
    with urllib.request.urlopen('http://127.0.0.1:8099/ping', timeout=5) as result:
        print('CONTROL_HTTP_STATUS', result.status)
except urllib.error.HTTPError as exc:
    print('CONTROL_HTTP_STATUS', exc.code)
"""
    control_result = inner_docker("exec", "fcai-ssrf-sink", "python", "-c", control)
    if "CONTROL_HTTP_STATUS " not in control_result.stdout:
        raise RuntimeError("positive-control private sink did not serve HTTP")
    baseline = private_request_count()
    if baseline < 1:
        raise RuntimeError("private sink log did not record positive-control request")

    sink_url = f"http://{sink_ip}:8099/ping"
    fixture_url = f"http://{public_ip}/"
    outcome = inner_docker(
        "exec", "fcai-crawl4ai", "python", "-c", CHROMIUM_PROBE,
        sink_url, fixture_url, timeout=45,
    )
    blocked = outcome.stdout.count("BROWSER_EGRESS_BLOCKED")
    if "BROWSER_STARTED" not in outcome.stdout or blocked != 2:
        raise RuntimeError("Chromium started incompletely or did not block both direct connections")
    after = private_request_count()
    if after != baseline:
        raise RuntimeError(
            f"private sink observed unexpected request: baseline={baseline}, after={after}"
        )
    print("PASS: Chromium launched and two direct connections were blocked")
    print(
        f"PASS: private sink positive control={baseline}, "
        f"after={after}; zero additional requests"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            ValueError, RuntimeError, KeyError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
