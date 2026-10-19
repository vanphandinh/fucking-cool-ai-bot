"""Regression checks for the non-disruptive VPS security report."""

from __future__ import annotations

import copy
import io
import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from scripts import crawl4ai_security_audit as audit


ROOT = Path(__file__).resolve().parents[1]


def compose_config() -> dict:
    return {
        "networks": {
            name: {"internal": name != "crawl4ai_uplink", "enable_ipv6": False}
            for name in ("crawl4ai_api", "crawl4ai_proxy", "crawl4ai_dns", "crawl4ai_uplink")
        },
        "services": {
            "bot": {"networks": {"default": {}, "crawl4ai_api": {}}},
            "crawl4ai": {
                "networks": {name: {} for name in (
                    "crawl4ai_api", "crawl4ai_proxy", "crawl4ai_dns")},
                "dns": ["10.221.57.53"],
            },
            "crawl4ai-proxy": {
                "networks": {"crawl4ai_proxy": {}, "crawl4ai_uplink": {}},
            },
            "crawl4ai-dns": {
                "networks": {
                    "crawl4ai_dns": {"ipv4_address": "10.221.57.53"},
                    "crawl4ai_uplink": {},
                },
                "read_only": True, "cap_drop": ["ALL"],
                "cap_add": ["NET_BIND_SERVICE"],
            },
        },
    }


def assert_compose(config: dict) -> None:
    with patch.object(audit, "command", return_value=json.dumps(config)):
        audit.check_compose()


def test_compose_rejects_network_and_privilege_regressions():
    base = compose_config()
    assert_compose(base)
    for change in (
        lambda conf: conf["services"]["crawl4ai"]["networks"].update(
            {"crawl4ai_uplink": {}}),
        lambda conf: conf["services"]["crawl4ai"].update({"privileged": True}),
        lambda conf: conf["services"]["crawl4ai-proxy"].update({"ports": ["3128:3128"]}),
        lambda conf: conf["services"]["crawl4ai"].update({"restart": "unless-stopped"}),
        lambda conf: conf["services"]["crawl4ai-dns"].update({"cap_add": ["NET_ADMIN"]}),
        lambda conf: conf["networks"]["crawl4ai_proxy"].update({"internal": False}),
    ):
        candidate = copy.deepcopy(base)
        change(candidate)
        with unittest.TestCase().assertRaises(RuntimeError):
            assert_compose(candidate)


@unittest.skipUnless(shutil.which("docker"), "Docker CLI not installed")
def test_current_compose_is_safe():
    audit.check_compose()


def test_proxy_dns_acl_and_detection_of_unsafe_allow(tmp_path):
    audit.check_proxy_and_dns()
    crawl = tmp_path / "crawl4ai"
    crawl.mkdir()
    original = ROOT / "crawl4ai"
    for filename in ("squid.conf", "Corefile"):
        (crawl / filename).write_text((original / filename).read_text(encoding="utf-8"),
                                      encoding="utf-8")
    with patch.object(audit, "ROOT", tmp_path):
        path = crawl / "squid.conf"
        path.write_text("http_access allow localhost\n" + path.read_text(encoding="utf-8"),
                        encoding="utf-8")
        with unittest.TestCase().assertRaises(RuntimeError):
            audit.check_proxy_and_dns()


def test_boot_service_only_queries_status():
    calls = []

    def read_only(*args):
        calls.append(args)
        if "cat" in args:
            return (
                "After=docker.service\n"
                "ExecStart=/bin/bash /repo/scripts/install_crawl4ai_firewall_boot.sh --boot"
            )
        if "show" in args:
            return "success"
        if "is-active" in args:
            return "active"
        return "enabled"

    with patch.object(audit, "command", side_effect=read_only):
        audit.check_boot_service()
    assert [args[1] for args in calls] == ["is-enabled", "is-active", "show", "cat"]


def test_live_restart_policies_require_all_existing_containers_disabled():
    present = "fcai-crawl4ai\nfcai-crawl4ai-proxy\nfcai-crawl4ai-dns"

    def inspect_policy(*args):
        if args[1] == "ps":
            return present
        return "no"

    with patch.object(audit, "command", side_effect=inspect_policy) as commands:
        audit.check_live_restart_policies()
    assert commands.call_count == 4

    def legacy_policy(*args):
        if args[1] == "ps":
            return present
        return "unless-stopped" if args[-1] == "fcai-crawl4ai" else "no"

    with patch.object(audit, "command", side_effect=legacy_policy):
        with unittest.TestCase().assertRaises(RuntimeError):
            audit.check_live_restart_policies()

    with patch.object(audit, "command", return_value=""):
        audit.check_live_restart_policies()  # All three absent: safely dormant.


def test_unverified_never_claimed_as_full_security_pass(capsys):
    with (patch.object(audit, "check_compose"),
          patch.object(audit, "check_proxy_and_dns"),
          patch.object(audit, "check_boot_service"),
          patch.object(audit, "check_live_restart_policies"),
          patch.object(audit, "check_reboot_evidence", side_effect=OSError("not found"))):
        assert audit.main() == 0
    output = capsys.readouterr().out
    assert "UNVERIFIED: Real reboot evidence" in output
    assert "UNVERIFIED: controlled public SSRF fixtures" in output
    assert "UNVERIFIED: firewall protection during Docker startup" in output


def test_static_network_failure_is_reported_without_sensitive_details(capsys):
    with (patch.object(audit, "check_compose", side_effect=RuntimeError("token-secret")),
          patch.object(audit, "check_proxy_and_dns"),
          patch.object(audit, "check_boot_service"),
          patch.object(audit, "check_live_restart_policies"),
          patch.object(audit, "check_reboot_evidence")):
        assert audit.main() == 1
    output = capsys.readouterr().out
    assert "FAIL: Docker network topology" in output
    assert "token-secret" not in output


def test_single_command_does_not_contain_destructive_operations():
    entry = (ROOT / "scripts" / "verify_crawl4ai_security.sh").read_text(encoding="utf-8")
    report = (ROOT / "scripts" / "check_crawl4ai_report.sh").read_text(encoding="utf-8")
    assert "check_crawl4ai_report.sh" in entry
    for text in ("docker compose restart", "docker compose stop", "docker compose up",
                 "crawl4ai_firewall.sh apply", "systemctl restart", "reboot"):
        assert text not in "\n".join(
            line for line in (entry + report).splitlines()
            if line.lstrip() and not line.lstrip().startswith("#")
        )


class TestAuditInUnittestCI(unittest.TestCase):
    """Bridge every pytest-style module regression into unittest discovery."""

    def test_compose_rejects_network_and_privilege_regressions(self):
        test_compose_rejects_network_and_privilege_regressions()

    def test_current_compose_is_safe(self):
        test_current_compose_is_safe()

    def test_proxy_dns_acl_and_detection_of_unsafe_allow(self):
        with tempfile.TemporaryDirectory() as directory:
            test_proxy_dns_acl_and_detection_of_unsafe_allow(Path(directory))

    def test_boot_service_only_queries_status(self):
        test_boot_service_only_queries_status()

    def test_live_restart_policies_require_all_existing_containers_disabled(self):
        test_live_restart_policies_require_all_existing_containers_disabled()

    def test_unverified_never_claimed_as_full_security_pass(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        capture = SimpleNamespace(
            readouterr=lambda: SimpleNamespace(out=stdout.getvalue(), err=stderr.getvalue())
        )
        with redirect_stdout(stdout), redirect_stderr(stderr):
            test_unverified_never_claimed_as_full_security_pass(capture)

    def test_static_network_failure_is_reported_without_sensitive_details(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        capture = SimpleNamespace(
            readouterr=lambda: SimpleNamespace(out=stdout.getvalue(), err=stderr.getvalue())
        )
        with redirect_stdout(stdout), redirect_stderr(stderr):
            test_static_network_failure_is_reported_without_sensitive_details(capture)

    def test_single_command_does_not_contain_destructive_operations(self):
        test_single_command_does_not_contain_destructive_operations()
