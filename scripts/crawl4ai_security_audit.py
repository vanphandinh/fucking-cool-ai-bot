"""Read-only static and boot-evidence checks for Crawl4AI's VPS report.

Print only fixed status messages. Never expose rendered Compose configuration,
Docker environment variables, tokens, or raw systemd output.
"""

from __future__ import annotations

import ipaddress
import json
import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REBOOT_STATE = Path("/var/tmp/fcai-crawl4ai-reboot-state.json")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
SERVICE = "fcai-crawl4ai-firewall.service"


def require(condition: bool) -> None:
    if not condition:
        raise RuntimeError("security invariant failed")


def command(*args: str) -> str:
    result = subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode:
        raise RuntimeError("required command failed")
    return result.stdout.strip()


def check_compose() -> None:
    config = json.loads(command(
        "docker", "compose", "--profile", "crawl4ai", "config",
        "--no-env-resolution", "--format", "json",
    ))
    services = config["services"]
    networks = config["networks"]
    for name in ("crawl4ai_api", "crawl4ai_proxy", "crawl4ai_dns"):
        require(networks[name]["internal"] is True)
        require(networks[name]["enable_ipv6"] is False)
    require(networks["crawl4ai_uplink"].get("internal", False) is False)
    require(networks["crawl4ai_uplink"]["enable_ipv6"] is False)

    expected = {
        "crawl4ai": {"crawl4ai_api", "crawl4ai_proxy", "crawl4ai_dns"},
        "crawl4ai-proxy": {"crawl4ai_proxy", "crawl4ai_uplink"},
        "crawl4ai-dns": {"crawl4ai_dns", "crawl4ai_uplink"},
    }
    for service_name, attached in expected.items():
        service = services[service_name]
        require(set(service["networks"]) == attached)
        require(not service.get("ports"))
        require(not service.get("privileged", False))
        require(not service.get("network_mode"))
        require("NET_ADMIN" not in service.get("cap_add", []))
    require("crawl4ai_uplink" not in services["bot"]["networks"])
    require(services["crawl4ai-dns"]["read_only"] is True)
    require(services["crawl4ai-dns"]["cap_drop"] == ["ALL"])
    require(services["crawl4ai-dns"]["cap_add"] == ["NET_BIND_SERVICE"])
    require(services["crawl4ai"]["dns"] == [
        services["crawl4ai-dns"]["networks"]["crawl4ai_dns"]["ipv4_address"]
    ])


def check_proxy_and_dns() -> None:
    squid = (ROOT / "crawl4ai" / "squid.conf").read_text(encoding="utf-8")
    directives = [line.strip() for line in squid.splitlines()
                  if line.strip() and not line.lstrip().startswith("#")]
    required = (
        "http_access deny !Safe_ports",
        "http_access deny CONNECT !SSL_ports",
        "http_access deny forbidden_dst",
        "http_access deny forbidden_dst_v6",
    )
    allow = directives.index("http_access allow all")
    require(all(directives.index(rule) < allow for rule in required))
    require(directives.index("http_access deny all") > allow)
    require([line for line in directives if line.startswith("http_access allow ")] == [
        "http_access allow all"
    ])
    require("cache deny all" in directives)
    require([line for line in directives if line.startswith("acl SSL_ports port ")] == [
        "acl SSL_ports port 443"
    ])
    require([line for line in directives if line.startswith("acl Safe_ports port ")] == [
        "acl Safe_ports port 80 443"
    ])
    expected = {
        "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
        "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
        "192.168.0.0/16", "198.18.0.0/15", "198.51.100.0/24",
        "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
    }
    cidrs = next(line.split()[3:] for line in directives
                 if line.startswith("acl forbidden_dst dst "))
    require(expected <= {str(ipaddress.ip_network(cidr)) for cidr in cidrs})
    require("acl forbidden_dst_v6 dst ::/128 ::1/128 fc00::/7 fe80::/10" in squid)
    corefile = (ROOT / "crawl4ai" / "Corefile").read_text(encoding="utf-8")
    require("forward . 127.0.0.11" in corefile)
    require("template IN AAAA ." in corefile)


def check_boot_service() -> None:
    require(command("systemctl", "is-enabled", SERVICE) == "enabled")
    require(command("systemctl", "is-active", SERVICE) == "active")
    require(command("systemctl", "show", SERVICE, "--property=Result", "--value") == "success")
    unit = command("systemctl", "cat", SERVICE)
    require("install_crawl4ai_firewall_boot.sh --boot" in unit)
    require("After=docker.service" in unit)


def check_reboot_evidence() -> None:
    fd = os.open(REBOOT_STATE, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        require(stat.S_ISREG(metadata.st_mode))
        require(metadata.st_uid == os.geteuid() and not metadata.st_mode & 0o077)
        state = json.load(stream)
    require(state["version"] == 1)
    require(state["preflight"] == state["postflight"] == "PASS")
    require(state["boot_id"] != state["after_boot_id"])
    require(state["after_boot_id"] == BOOT_ID.read_text(encoding="ascii").strip())
    require(state["revision"] == command("git", "rev-parse", "HEAD"))


def main() -> int:
    failures = 0
    for label, check, missing_is_pending in (
        ("Docker network topology and container permissions", check_compose, False),
        ("Squid ACL and DNS configuration", check_proxy_and_dns, False),
        ("Firewall boot service active", check_boot_service, False),
        ("Real reboot evidence for this Git revision", check_reboot_evidence, True),
    ):
        try:
            check()
        except (AssertionError, KeyError, IndexError, StopIteration, OSError,
                ValueError, RuntimeError, subprocess.TimeoutExpired, TypeError):
            if missing_is_pending:
                print(f"UNVERIFIED: {label}")
            else:
                print(f"FAIL: {label}")
                failures += 1
        else:
            print(f"PASS: {label}")
    print("UNVERIFIED: controlled public SSRF fixtures and independent sink/DNS logs")
    print("UNVERIFIED: firewall protection during Docker startup interval")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
