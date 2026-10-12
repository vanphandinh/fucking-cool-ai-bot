"""Opt-in staging test of the real Crawl4AI container and a private request sink.

All fixture URLs MUST be hosted on public infrastructure controlled by this
project. The test never performs third-party SSRF probes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


def docker(*args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["docker", *args], check=False, capture_output=True, text=True, timeout=120
    )
    if check and completed.returncode:
        raise RuntimeError(completed.stderr.strip())
    return (completed.stdout + completed.stderr).strip()


def probe_crawl(url: str, *, success_required: bool = False,
                failure_required: bool = False) -> None:
    code = (
        "import asyncio,sys; from app.config import Settings; "
        "from app.search.crawl4ai_client import read_page; "
        "r=asyncio.run(read_page(sys.argv[1],Settings())); "
        "print('ok='+str(r.ok)+' reason='+r.reason); "
        "sys.exit(0 if r.ok else 1)"
    )
    result = subprocess.run(
        [
            "docker", "exec", "-e", "CRAWL4AI_ENABLED=1",
            "-e", "CRAWL4AI_EGRESS_PROXY_URL=http://crawl4ai-proxy:3128",
            "fcai-bot", "python", "-c", code, url,
        ],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if success_required and result.returncode:
        raise AssertionError(
            f"public crawl failed: {result.stdout.strip()} {result.stderr.strip()}"
        )
    if failure_required and result.returncode == 0:
        raise AssertionError("blocked URL was reported as a successful crawl")
    print(f"crawl exit={result.returncode} {result.stdout.strip()}")


def sink_requests() -> int:
    return docker("logs", "fcai-ssrf-sink").count("SINK_REQUEST")


def assert_sink_untouched(label: str, baseline: int) -> None:
    time.sleep(0.5)
    observed = sink_requests()
    if observed != baseline:
        raise AssertionError(
            f"{label}: PRIVATE REQUEST RECEIVED (baseline={baseline}, now={observed})"
        )
    print(f"{label}: private sink received zero additional requests")


def probe_direct_chromium(*urls: str) -> None:
    """Prove an actual unproxied Chromium cannot reach controlled test targets.

    A raw urllib socket check alone does not exercise the browser's networking
    stack, so require Chromium to launch and each navigation to fail at egress.
    """
    browser_probe = """
import asyncio
import sys
from playwright.async_api import (
    Error as BrowserError,
    TimeoutError as BrowserTimeout,
    async_playwright,
)

async def check():
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True, args=['--no-sandbox'])
        except Exception as exc:
            print('BROWSER_START_FAILED', type(exc).__name__, file=sys.stderr)
            return 2
        print('BROWSER_STARTED', flush=True)
        try:
            page = await browser.new_page()
            for url in sys.argv[1:]:
                try:
                    response = await page.goto(url, timeout=6000, wait_until='domcontentloaded')
                except BrowserError as exc:
                    message = str(exc)
                    if not isinstance(exc, BrowserTimeout) and not any(
                        marker in message for marker in (
                            'net::ERR_ADDRESS_UNREACHABLE',
                            'net::ERR_TIMED_OUT',
                            'net::ERR_CONNECTION_TIMED_OUT',
                            'net::ERR_CONNECTION_REFUSED',
                        )
                    ):
                        print('BROWSER_UNEXPECTED_ERROR', type(exc).__name__, file=sys.stderr)
                        return 2
                    print('BROWSER_EGRESS_BLOCKED', flush=True)
                    continue
                print(
                    'BROWSER_DIRECT_ACCESS',
                    response.status if response else 'no-response',
                    file=sys.stderr,
                )
                return 1
            return 0
        finally:
            await browser.close()

raise SystemExit(asyncio.run(check()))
"""
    result = subprocess.run(
        ["docker", "exec", "fcai-crawl4ai", "python", "-c", browser_probe, *urls],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if (result.returncode or "BROWSER_STARTED" not in result.stdout
            or result.stdout.count("BROWSER_EGRESS_BLOCKED") != len(urls)):
        raise AssertionError(
            "unproxied Chromium bypass test failed: "
            f"exit={result.returncode} output={result.stdout.strip()} "
            f"error={result.stderr.strip()}"
        )
    print(f"unproxied Chromium: {len(urls)} controlled destinations blocked")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-url", required=True)
    parser.add_argument("--redirect-url", required=True,
                        help="Controlled public fixture redirecting to {sink_url}")
    parser.add_argument("--subresource-url", required=True,
                        help="Controlled public HTML fixture loading {sink_url} as img/iframe")
    parser.add_argument("--rebind-url", required=True,
                        help="Controlled DNS fixture re-resolving public to private sink")
    args = parser.parse_args()
    if sys.platform != "linux":
        raise RuntimeError("isolated Docker firewall tests must run on the Linux staging VPS")

    firewall = Path(__file__).with_name("crawl4ai_firewall.sh")
    subprocess.run(["bash", str(firewall), "check"], check=True, timeout=30)
    # Never replace or delete an operator's existing test sink.
    existing = docker(
        "ps", "-a", "--filter", "name=^/fcai-ssrf-sink$", "--format", "{{.Names}}"
    )
    if existing:
        raise RuntimeError("fcai-ssrf-sink already exists; choose a clean staging test environment")
    sink_code = (
        "from http.server import BaseHTTPRequestHandler,HTTPServer; "
        "H=type('H',(BaseHTTPRequestHandler,),{'do_GET':lambda s: "
        "(print('SINK_REQUEST',s.path,flush=True),s.send_response(204),s.end_headers())}); "
        "HTTPServer(('0.0.0.0',8099),H).serve_forever()"
    )
    docker(
        "run", "--rm", "-d", "--name", "fcai-ssrf-sink",
        "--network", "fcai-crawl4ai-api", "python:3.12-alpine", "python", "-u",
        "-c", sink_code,
    )
    try:
        # A positive-control request from inside the sink's own network
        # namespace proves that its log counter actually works. All later
        # checks compare against this known baseline.
        time.sleep(1)
        docker(
            "exec", "fcai-ssrf-sink", "python", "-c",
            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/control',timeout=5).close()",
        )
        baseline = sink_requests()
        if baseline != 1:
            raise AssertionError(f"private sink logging is not reliable: {baseline=}")
        sink_ip = docker(
            "inspect", "--format",
            '{{with index .NetworkSettings.Networks "fcai-crawl4ai-api"}}{{.IPAddress}}{{end}}',
            "fcai-ssrf-sink",
        )
        sink_url = f"http://{sink_ip}:8099/ping"
        print(f"private sink: {sink_url}")
        probe_crawl(args.public_url, success_required=True)
        assert_sink_untouched("public", baseline)
        for blocked in (
            sink_url, "http://127.0.0.1:8099/ping",
            "http://10.0.0.1:8099/ping", "http://169.254.169.254/latest/meta-data/",
        ):
            probe_crawl(blocked, failure_required=True)
        assert_sink_untouched("direct private URL", baseline)
        # A direct network connection bypassing the browser proxy MUST fail.
        bypass = subprocess.run(
            ["docker", "exec", "fcai-crawl4ai", "python", "-c",
             "import sys,urllib.request; urllib.request.urlopen(sys.argv[1],timeout=3)",
             sink_url],
            check=False, capture_output=True, text=True, timeout=20,
        )
        if bypass.returncode == 0:
            raise AssertionError("direct crawler connection unexpectedly succeeded")
        assert_sink_untouched("direct connection bypass", baseline)
        print("direct bypass: connection refused or filtered")
        probe_direct_chromium(sink_url, args.public_url)
        assert_sink_untouched("unproxied Chromium bypass", baseline)
        for label, template in (
            ("redirect", args.redirect_url),
            ("image/iframe", args.subresource_url),
            ("DNS rebinding", args.rebind_url),
        ):
            probe_crawl(
                template.replace("{sink_url}", sink_url),
                failure_required=(label in ("redirect", "DNS rebinding")),
            )
            assert_sink_untouched(label, baseline)
        subprocess.run(["bash", str(firewall), "check"], check=True, timeout=30)
        print("ISOLATION PASS: all private request counts were zero")
        return 0
    finally:
        docker("rm", "-f", "fcai-ssrf-sink", check=False)


if __name__ == "__main__":
    raise SystemExit(main())
