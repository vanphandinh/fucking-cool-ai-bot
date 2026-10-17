"""The type-check ratchet must fail on growth and never pass when it cannot run.

Report mode is only useful if the failure direction is right: a new finding has to
fail, a missing checker must not look like a pass, and the baseline must be the
deliberately recorded level rather than whatever the last run produced.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_types import (
    compare,
    load_baseline,
    main,
    parse_pyright_report,
    pin_command,
    resolve_command,
    tool_version,
    version_token,
)

ROOT = Path(__file__).parents[1]


def _report(errors: int, *, rule: str = "reportArgumentType") -> dict:
    return {
        "version": "1.1.0",
        "summary": {"filesAnalyzed": 3, "errorCount": errors, "warningCount": 0},
        "generalDiagnostics": [
            {
                "file": f"/repo/app/module_{index}.py",
                "severity": "error",
                "message": "boom",
                "rule": rule,
            }
            for index in range(errors)
        ]
        + [
            {
                "file": "/repo/app/ok.py",
                "severity": "warning",
                "message": "noise",
                "rule": "reportUnusedVariable",
            }
        ],
    }


class ReportParsingTests(unittest.TestCase):
    def test_errors_are_counted_and_grouped_by_rule_and_file(self):
        measured = parse_pyright_report(_report(3))
        self.assertEqual(measured["errors"], 3)
        self.assertEqual(measured["files_analyzed"], 3)
        self.assertEqual(measured["rule_counts"], {"reportArgumentType": 3})
        self.assertEqual(len(measured["file_counts"]), 3)

    def test_warnings_are_not_counted_as_errors(self):
        measured = parse_pyright_report(_report(0))
        self.assertEqual(measured["errors"], 0)
        self.assertEqual(measured["rule_counts"], {})

    def test_missing_fields_do_not_crash_the_counter(self):
        measured = parse_pyright_report({})
        self.assertEqual(measured["errors"], 0)
        self.assertEqual(measured["files_analyzed"], 0)


class ToolVersionTests(unittest.TestCase):
    def test_version_is_read_from_the_top_level_field_pyright_emits(self):
        self.assertEqual(tool_version(_report(1)), "1.1.0")

    def test_version_reporting_variants_normalise_to_the_bare_version(self):
        self.assertEqual(version_token("pyright 1.1.414"), "1.1.414")
        self.assertEqual(version_token("1.1.414"), "1.1.414")
        self.assertIsNone(version_token(None))
        self.assertIsNone(version_token("unknown"))

    def test_pinning_rewrites_the_package_spec_once(self):
        command = ("npx", "--yes", "pyright", "--outputjson")
        self.assertEqual(
            pin_command(command, pin="1.1.414"),
            ("npx", "--yes", "pyright@1.1.414", "--outputjson"),
        )

    def test_pinning_respects_an_explicit_version_and_other_tools(self):
        explicit = ("npx", "--yes", "pyright@1.2.0", "--outputjson")
        self.assertEqual(pin_command(explicit, pin="1.1.414"), explicit)
        other = ("python", "-m", "mypy", "--outputjson")
        self.assertEqual(pin_command(other, pin="1.1.414"), other)
        self.assertEqual(pin_command(explicit, pin=None), explicit)


class ComparisonTests(unittest.TestCase):
    def test_growth_fails_and_improvement_below_baseline_passes(self):
        self.assertEqual(compare(55, 56), "worse")
        self.assertEqual(compare(55, 55), "equal")
        self.assertEqual(compare(55, 54), "better")

    def test_unbaselined_repository_is_reported_as_such(self):
        self.assertEqual(compare(None, 3), "unbaselined")


class CommandLineTests(unittest.TestCase):
    def _run(
        self,
        *,
        measured_errors: int,
        baseline_errors: int | None,
        measured_version: str = "1.1.0",
        baseline_version: str | None = None,
    ) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            report = _report(measured_errors)
            report["version"] = measured_version
            report_path = Path(tmp) / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            baseline_path = Path(tmp) / "baseline.json"
            if baseline_errors is not None:
                payload: dict = {"errors": baseline_errors}
                if baseline_version is not None:
                    payload["tool_version"] = baseline_version
                baseline_path.write_text(json.dumps(payload), encoding="utf-8")
            return main(
                ["--report", str(report_path), "--baseline", str(baseline_path)]
            )

    def test_growth_exits_one(self):
        self.assertEqual(self._run(measured_errors=56, baseline_errors=55), 1)

    def test_equal_or_better_exits_zero(self):
        self.assertEqual(self._run(measured_errors=55, baseline_errors=55), 0)
        self.assertEqual(self._run(measured_errors=50, baseline_errors=55), 0)

    def test_unbaselined_exits_zero_without_claiming_a_pass(self):
        self.assertEqual(self._run(measured_errors=10, baseline_errors=None), 0)

    def test_a_different_checker_version_is_not_a_verdict(self):
        """A newer pyright can report more; that is not the code regressing."""
        self.assertEqual(
            self._run(
                measured_errors=56,
                baseline_errors=55,
                measured_version="1.1.415",
                baseline_version="pyright 1.1.414",
            ),
            2,
        )

    def test_the_same_version_still_reports_growth_and_equality(self):
        self.assertEqual(
            self._run(
                measured_errors=56,
                baseline_errors=55,
                measured_version="pyright 1.1.414",
                baseline_version="pyright 1.1.414",
            ),
            1,
        )
        self.assertEqual(
            self._run(
                measured_errors=55,
                baseline_errors=55,
                measured_version="1.1.414",
                baseline_version="pyright 1.1.414",
            ),
            0,
        )

    def test_missing_tool_is_not_a_pass(self):
        code = main(["--command", "definitely-not-installed-xyz --outputjson"])
        self.assertEqual(code, 2)

    def test_repository_baseline_matches_its_documented_shape(self):
        baseline = load_baseline(ROOT / "pyright_baseline.json")
        self.assertIsInstance(baseline["errors"], int)
        self.assertGreaterEqual(baseline["errors"], 0)
        self.assertIn("rule_counts", baseline)
        self.assertIn("tool_version", baseline)


class CommandResolutionTests(unittest.TestCase):
    def test_unresolvable_executable_is_reported_as_missing(self):
        self.assertIsNone(resolve_command(("definitely-not-installed-xyz",)))

    def test_resolved_executable_keeps_its_arguments(self):
        argv = resolve_command(("python", "-c", "print(1)"))
        self.assertIsNotNone(argv)
        self.assertEqual(argv[1:], ["-c", "print(1)"])


if __name__ == "__main__":
    unittest.main()
