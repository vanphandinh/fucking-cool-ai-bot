"""Reject inconclusive results from the opt-in SSRF staging runner."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from scripts import test_crawl4ai_isolation as isolation


class TestCrawl4AIIsolationRunner(unittest.TestCase):
    def test_denied_url_requires_real_isolation_result(self) -> None:
        cases = [
            (2, "", "did not execute read_page"),
            (1, "Traceback (most recent call last):", "did not execute read_page"),
            (1, "ok=False reason=disabled", "expected isolation reason"),
            (1, "ok=False reason=network", "expected isolation reason"),
            (1, "ok=True reason=", "inconsistent result"),
            (0, "ok=False reason=blocked_url", "inconsistent result"),
            (0, "ok=True reason=", "blocked URL was reported as a successful crawl"),
        ]
        for returncode, output, expected_error in cases:
            with self.subTest(returncode=returncode, output=output):
                result = subprocess.CompletedProcess(
                    [], returncode, stdout=output, stderr=""
                )
                with patch.object(isolation.subprocess, "run", return_value=result):
                    with self.assertRaisesRegex(AssertionError, expected_error):
                        isolation.probe_crawl(
                            "http://10.0.0.1/", failure_required=True,
                            expected_reasons=frozenset({"blocked_url"}),
                        )

    def test_real_crawl_results_are_accepted(self) -> None:
        cases = [
            (0, "ok=True reason=", True, False, None),
            (1, "ok=False reason=blocked_url", False, True,
             frozenset({"blocked_url"})),
            (1, "ok=False reason=crawl_failed", False, True,
             frozenset({"blocked_redirect", "crawl_failed"})),
        ]
        for returncode, output, success_required, failure_required, reasons in cases:
            with self.subTest(returncode=returncode, output=output):
                result = subprocess.CompletedProcess(
                    [], returncode, stdout=output, stderr=""
                )
                with patch.object(isolation.subprocess, "run", return_value=result):
                    isolation.probe_crawl(
                        "https://fixture.example/", success_required=success_required,
                        failure_required=failure_required, expected_reasons=reasons,
                    )

    def test_public_fixture_must_load(self) -> None:
        result = subprocess.CompletedProcess(
            [], 1, stdout="ok=False reason=crawl_failed", stderr=""
        )
        with patch.object(isolation.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(AssertionError, "public crawl failed"):
                isolation.probe_crawl(
                    "https://fixture.example/subresources", success_required=True
                )


if __name__ == "__main__":
    unittest.main()
