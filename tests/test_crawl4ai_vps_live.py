"""Check that the VPS live probe remains opt-in and non-disruptive."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from scripts import check_crawl4ai_vps_live as live


def test_live_probe_uses_existing_containers_without_mutation(tmp_path, capsys):
    (tmp_path / ".env").write_text("CRAWL4AI_ENABLED=0\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def execute(argv, **kwargs):
        assert kwargs["cwd"] == tmp_path
        calls.append(tuple(argv))
        output = "Ubuntu Linux" if argv[:2] == ("docker", "info") else "PASS probe"
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    with (patch.object(live, "ROOT", tmp_path),
          patch.object(live.sys, "platform", "linux"),
          patch.object(live.os, "geteuid", return_value=0, create=True),
          patch("sys.argv", ["check_crawl4ai_vps_live.py", "--live"]),
          patch.object(live.subprocess, "run", side_effect=execute)):
        assert live.main() == 0

    assert len(calls) == 7
    assert sum(args[0] == "bash" and args[-1] == "check" for args in calls) == 2
    assert sum(args[:3] == ("docker", "exec", "fcai-crawl4ai") for args in calls) == 3
    assert not any("restart" in args or "stop" in args or "apply" in args
                   or "up" in args for args in calls)
    assert "LIVE EGRESS PASS" in capsys.readouterr().out


def test_live_probe_refuses_implicit_activation_and_missing_enabled_setting(tmp_path):
    (tmp_path / ".env").write_text("CRAWL4AI_ENABLED=1\n", encoding="utf-8")
    with patch.object(live, "ROOT", tmp_path):
        try:
            live.require_disabled()
        except RuntimeError as exc:
            assert "explicitly disable" in str(exc)
        else:
            raise AssertionError("active production crawler must be refused")


def test_failed_probe_never_reports_pass(tmp_path, capsys):
    (tmp_path / ".env").write_text("CRAWL4AI_ENABLED=0\n", encoding="utf-8")

    def execute(argv, **kwargs):
        if argv[:3] == ("docker", "exec", "fcai-crawl4ai"):
            return subprocess.CompletedProcess(argv, 1, stdout="FAIL connected", stderr="")
        output = "Ubuntu Linux" if argv[:2] == ("docker", "info") else ""
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    with (patch.object(live, "ROOT", tmp_path),
          patch.object(live.sys, "platform", "linux"),
          patch.object(live.os, "geteuid", return_value=0, create=True),
          patch("sys.argv", ["check_crawl4ai_vps_live.py", "--live"]),
          patch.object(live.subprocess, "run", side_effect=execute)):
        assert live.main() == 1
    assert "LIVE EGRESS INCOMPLETE" in capsys.readouterr().err
