"""Reject inconclusive results from the opt-in SSRF staging runner."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from scripts import test_crawl4ai_isolation as isolation


@pytest.mark.parametrize(
    ("returncode", "output", "expected_error"),
    [
        (2, "", "did not execute read_page"),
        (1, "Traceback (most recent call last):", "did not execute read_page"),
        (1, "ok=False reason=disabled", "expected isolation reason"),
        (1, "ok=False reason=network", "expected isolation reason"),
        (1, "ok=True reason=", "inconsistent result"),
        (0, "ok=False reason=blocked_url", "inconsistent result"),
        (0, "ok=True reason=", "blocked URL was reported as a successful crawl"),
    ],
)
def test_denied_url_requires_real_isolation_result(
    returncode: int, output: str, expected_error: str
) -> None:
    result = subprocess.CompletedProcess([], returncode, stdout=output, stderr="")
    with patch.object(isolation.subprocess, "run", return_value=result):
        with pytest.raises(AssertionError, match=expected_error):
            isolation.probe_crawl(
                "http://10.0.0.1/", failure_required=True,
                expected_reasons=frozenset({"blocked_url"}),
            )


@pytest.mark.parametrize(
    ("returncode", "output", "success_required", "failure_required", "reasons"),
    [
        (0, "ok=True reason=", True, False, None),
        (1, "ok=False reason=blocked_url", False, True, frozenset({"blocked_url"})),
        (1, "ok=False reason=crawl_failed", False, True,
         frozenset({"blocked_redirect", "crawl_failed"})),
    ],
)
def test_real_crawl_results_are_accepted(
    returncode: int, output: str, success_required: bool, failure_required: bool,
    reasons: frozenset[str] | None,
) -> None:
    result = subprocess.CompletedProcess([], returncode, stdout=output, stderr="")
    with patch.object(isolation.subprocess, "run", return_value=result):
        isolation.probe_crawl(
            "https://fixture.example/", success_required=success_required,
            failure_required=failure_required, expected_reasons=reasons,
        )


def test_public_fixture_must_load() -> None:
    result = subprocess.CompletedProcess(
        [], 1, stdout="ok=False reason=crawl_failed", stderr=""
    )
    with patch.object(isolation.subprocess, "run", return_value=result):
        with pytest.raises(AssertionError, match="public crawl failed"):
            isolation.probe_crawl("https://fixture.example/subresources", success_required=True)
