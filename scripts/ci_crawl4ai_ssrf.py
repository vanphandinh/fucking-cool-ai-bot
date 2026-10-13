"""Live SSRF regression in a throwaway GitHub Actions Docker-in-Docker engine.

Never use this with the host Docker daemon or production VPS. CI fixtures use
synthetic addresses that are reachable only within the disposable engine.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path


API_NET = "fcai-crawl4ai-api"
PROXY_NET = "fcai-crawl4ai-proxy"
UPLINK_NET = "fcai-crawl4ai-uplink"
PUBLIC_IP = "11.23.0.50"
PRIVATE_IP = "172.30.71.50"
PROXY_IP = "172.30.72.10"
TOKEN = secrets.token_hex(24)
WORKSPACE = Path("/workspace")


PUBLIC_SERVER = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        print('PUBLIC_GET', self.path, flush=True)
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'http://172.30.71.50:8099/ping')
            self.end_headers()
            return
        if self.path in ('/public.png', '/public-frame'):
            body = b'PUBLIC ASSET LOADED'
        else:
            assets = ''
            if self.path == '/rich':
                assets = ('<img src="/public.png"><iframe src="/public-frame"></iframe>'
                          '<img src="http://172.30.71.50:8099/private-image">'
                          '<iframe src="http://172.30.71.50:8099/private-frame"></iframe>')
            article = ' '.join([
                'Controlled public fixture for Crawl4AI isolation and browser rendering.'
            ] * 45)
            body = (f'<html><body><h1>Controlled public fixture</h1>'
                    f'<main><p>{article}</p></main>{assets}</body></html>').encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
HTTPServer(('0.0.0.0', 80), Handler).serve_forever()
'''

SINK_SERVER = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        print('SINK_REQUEST', self.path, flush=True)
        self.send_response(204)
        self.end_headers()
HTTPServer(('0.0.0.0', 8099), Handler).serve_forever()
'''


def run(*args: str, check: bool = True, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if check and result.returncode:
        raise RuntimeError(f"{args[0:3]} failed ({result.returncode}): {result.stderr[-1000:]}")
    return result


def docker(*args: str, check: bool = True, timeout: int = 180) -> str:
    result = run("docker", *args, check=check, timeout=timeout)
    return (result.stdout + result.stderr).strip()


def assert_isolated_engine() -> None:
    if (os.environ.get("CI") != "true"
            or os.environ.get("FCAI_ISOLATED_CI") != "1"
            or not Path("/.dockerenv").exists()
            or not (WORKSPACE / "scripts/crawl4ai_firewall.sh").is_file()):
        raise RuntimeError("Refusing to run outside the opt-in disposable CI engine")


def container_state(name: str) -> tuple[str, str]:
    state = docker("inspect", "--format", "{{.State.Status}}", name)
    policy = docker("inspect", "--format", "{{.HostConfig.RestartPolicy.Name}}", name)
    return state, policy


def check_dormant() -> None:
    for name in ("fcai-crawl4ai", "fcai-crawl4ai-proxy", "fcai-crawl4ai-dns"):
        state, policy = container_state(name)
        if policy != "no" or state == "running":
            raise AssertionError(f"Unsafe daemon startup: {name} state={state} restart={policy}")
        print(f"PASS: {name} stayed stopped after daemon restart (restart=no)", flush=True)
    print("PASS: CI Docker daemon restart was safe-dormant", flush=True)
    print("UNVERIFIED: physical VPS boot-time firewall window (requires target-host evidence)")


def wait_healthy(name: str, seconds: int = 180) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = docker("inspect", "--format", "{{.State.Health.Status}}", name)
        if status == "healthy":
            return
        if status == "unhealthy":
            raise AssertionError(f"Container {name} is unhealthy")
        time.sleep(3)
    raise TimeoutError(f"Container {name} never became healthy")


def sink_count() -> int:
    return docker("logs", "fcai-ssrf-sink").count("SINK_REQUEST")


def assert_sink(label: str, baseline: int) -> None:
    time.sleep(0.5)
    count = sink_count()
    if count != baseline:
        raise AssertionError(f"{label}: SSRF sink was reached ({baseline} -> {count})")
    print(f"PASS: {label}; no new private sink requests", flush=True)


def crawl(url: str, *, expect: str, contains: str = "Controlled public fixture") -> None:
    result = run(
        "docker", "exec", "-e", f"CRAWL4AI_TEST_TOKEN={TOKEN}",
        "fcai-bot", "python", "/probe.py", url, "--expect", expect,
        "--contains", contains, check=False, timeout=100,
    )
    print(f"/crawl {url} => {result.stdout.strip()}", flush=True)
    if result.returncode:
        raise AssertionError(
            f"Unexpected /crawl result ({result.returncode}): {result.stdout} {result.stderr}"
        )


def main() -> None:
    assert_isolated_engine()
    override = Path("/tmp/fcai-ci-override.yml")
    override.write_text(f"""
services:
  bot:
    env_file: /tmp/fcai-ci.env
  crawl4ai-proxy:
    networks:
      crawl4ai_proxy:
        ipv4_address: {PROXY_IP}
      crawl4ai_uplink: {{}}
  crawl4ai-dns:
    image: python:3.12-alpine
    entrypoint: [python, -u, /tmp/rebind.py]
    command: [--bind, 0.0.0.0, --public-ip, {PUBLIC_IP},
              --private-ip, {PRIVATE_IP}, --proxy-ip, {PROXY_IP}]
    volumes:
      - /workspace/scripts/crawl4ai_rebind_dns_fixture.py:/tmp/rebind.py:ro
networks:
  crawl4ai_api:
    ipam:
      config:
        - subnet: 172.30.71.0/24
  crawl4ai_proxy:
    ipam:
      config:
        - subnet: 172.30.72.0/24
  crawl4ai_uplink:
    ipam:
      config:
        - subnet: 11.23.0.0/24
""", encoding="utf-8")
    Path("/tmp/fcai-ci.env").write_text("CRAWL4AI_ENABLED=0\nBOT_TOKEN=\n", encoding="utf-8")
    env = os.environ.copy()
    env["CRAWL4AI_API_TOKEN"] = TOKEN
    command = ["docker", "compose", "-f", "/workspace/docker-compose.yml",
               "-f", str(override), "--env-file", "/tmp/fcai-ci.env",
               "--profile", "crawl4ai", "up", "-d",
               "crawl4ai", "crawl4ai-proxy", "crawl4ai-dns"]
    completed = subprocess.run(command, env=env, text=True, capture_output=True,
                               timeout=900, check=False)
    if completed.returncode:
        raise RuntimeError(f"Compose failed: {completed.stderr[-1500:]}")
    docker("run", "-d", "--name", "fcai-bot", "--network", API_NET,
           "python:3.12-alpine", "python", "-u", "-c", "import time;time.sleep(3600)")
    docker("run", "-d", "--name", "fcai-public-fixture", "--network", UPLINK_NET,
           "--ip", PUBLIC_IP, "python:3.12-alpine", "python", "-u", "-c", PUBLIC_SERVER)
    docker("run", "-d", "--name", "fcai-ssrf-sink", "--network", API_NET,
           "--ip", PRIVATE_IP, "python:3.12-alpine", "python", "-u", "-c", SINK_SERVER)
    docker("cp", "/workspace/scripts/crawl4ai_dind_probe.py", "fcai-bot:/probe.py")
    time.sleep(3)
    docker("exec", "fcai-ssrf-sink", "python", "-c",
           "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8099/control').close()")
    baseline = sink_count()
    if baseline != 1:
        raise AssertionError(f"Private sink positive control missing: {baseline}")
    print("PASS: private sink positive control recorded", flush=True)
    wait_healthy("fcai-crawl4ai")
    for name in ("fcai-crawl4ai", "fcai-crawl4ai-proxy", "fcai-crawl4ai-dns"):
        state, policy = container_state(name)
        if state != "running" or policy != "no":
            raise AssertionError(f"{name}: unexpected state={state} restart={policy}")
    run("bash", "/workspace/scripts/crawl4ai_firewall.sh", "apply")
    run("bash", "/workspace/scripts/crawl4ai_firewall.sh", "check")
    print("PASS: exact network topology and firewall rules", flush=True)
    crawl(f"http://{PUBLIC_IP}/", expect="success")
    assert_sink("public crawl", baseline)
    crawl(f"http://{PRIVATE_IP}:8099/ping", expect="blocked")
    assert_sink("direct private seed", baseline)
    crawl(f"http://{PUBLIC_IP}/redirect", expect="blocked")
    if "PUBLIC_GET /redirect" not in docker("logs", "fcai-public-fixture"):
        raise AssertionError("Redirect fixture was never requested")
    assert_sink("public redirect to private", baseline)
    crawl(f"http://{PUBLIC_IP}/rich", expect="success")
    public_log = docker("logs", "fcai-public-fixture")
    for path in ("/public.png", "/public-frame"):
        if f"PUBLIC_GET {path}" not in public_log:
            raise AssertionError(f"Browser did not load positive-control subresource: {path}")
    assert_sink("private image and iframe", baseline)
    hostname = f"ci-{secrets.token_hex(5)}.fcai.test"
    docker("exec", "fcai-crawl4ai", "python", "-c",
           "import socket,sys;print(socket.gethostbyname(sys.argv[1]))", hostname)
    crawl(f"http://{hostname}/", expect="blocked")
    dns_log = docker("logs", "fcai-crawl4ai-dns")
    if (f"DNS_QUERY host={hostname} type=1 answer={PUBLIC_IP}" not in dns_log
            or f"DNS_QUERY host={hostname} type=1 answer={PRIVATE_IP}" not in dns_log):
        raise AssertionError(
            "Rebinding hostname did not resolve to both public and private addresses"
        )
    assert_sink("DNS rebinding to private", baseline)
    run("bash", "/workspace/scripts/crawl4ai_firewall.sh", "check")
    print("PASS: live CI SSRF fixtures and firewall postflight", flush=True)
    print("UNVERIFIED: actual VPS host reboot and firewall startup interval", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--after-daemon-restart", action="store_true")
    opts = parser.parse_args()
    try:
        if opts.after_daemon_restart:
            assert_isolated_engine()
            check_dormant()
        else:
            main()
    except (AssertionError, OSError, RuntimeError, TimeoutError,
            subprocess.TimeoutExpired) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from None
