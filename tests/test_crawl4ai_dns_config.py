"""Configuration guards for Crawl4AI's isolated public DNS resolver.

The live SSRF and firewall-restart gates are exercised on Linux staging.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestCrawl4AIDnsConfig(unittest.TestCase):
    def test_coredns_forwards_via_docker_stub_and_disables_ipv6_answers(self) -> None:
        corefile = (ROOT / "crawl4ai" / "Corefile").read_text(encoding="utf-8")
        self.assertIn("template IN AAAA .", corefile)
        self.assertIn("rcode NOERROR", corefile)
        self.assertIn("forward . 127.0.0.11", corefile)
        self.assertNotIn("forward . 8.8.8.8", corefile)

    def test_firewall_guards_resolver_and_direct_crawler_egress(self) -> None:
        firewall = (ROOT / "scripts" / "crawl4ai_firewall.sh").read_text(
            encoding="utf-8"
        )
        for fragment in (
            'fcai-crawl4ai-dns',
            '"$crawler_dns_ip" -d "$dns_ip" -p udp --dport 53',
            '"$crawler_dns_ip" -d "$dns_ip" -p tcp --dport 53',
            '"$crawler_dns_ip" -j DROP',
            '"$dns_public_ip" -j DROP',
            '"$dns_ip" -j DROP',
            'ip saddr $crawler_dns_ip ip daddr $dns_ip udp dport 53 accept',
            'ip saddr $crawler_dns_ip ip daddr $dns_ip tcp dport 53 accept',
            'ip saddr $crawler_dns_ip drop',
            'ip saddr $dns_public_ip drop',
            'ip saddr $dns_ip drop',
            '"$dns_bridge"',
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, firewall)

    @unittest.skipUnless(shutil.which("docker"), "Docker CLI not installed")
    def test_compose_dns_is_private_version_pinned_and_unpublished(self) -> None:
        result = subprocess.run(
            ["docker", "compose", "--profile", "crawl4ai", "config",
             "--no-env-resolution", "--format", "json"],
            cwd=ROOT, check=True, capture_output=True, text=True, timeout=30,
        )
        config = json.loads(result.stdout)
        dns = config["services"]["crawl4ai-dns"]
        crawler = config["services"]["crawl4ai"]
        network = config["networks"]["crawl4ai_dns"]
        self.assertTrue(network["internal"])
        self.assertFalse(network["enable_ipv6"])
        self.assertEqual(crawler["dns"], [dns["networks"]["crawl4ai_dns"]["ipv4_address"]])
        self.assertIn("crawl4ai_dns", crawler["networks"])
        self.assertNotIn("crawl4ai_uplink", crawler["networks"])
        self.assertIn("crawl4ai_uplink", dns["networks"])
        self.assertNotIn("ports", dns)
        self.assertNotIn("ports", crawler)
        self.assertTrue(dns["read_only"])
        self.assertEqual(dns["cap_drop"], ["ALL"])
        self.assertEqual(dns["cap_add"], ["NET_BIND_SERVICE"])
        self.assertIn("@sha256:", dns["image"])


if __name__ == "__main__":
    unittest.main()
