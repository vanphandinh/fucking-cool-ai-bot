"""Static type check in report mode, ratcheted against a recorded baseline.

The repository has real type findings (mostly ``reportArgumentType`` and
``reportPossiblyUnboundVariable``) that predate any type checking. Demanding zero
today would mean either a large speculative refactor or a checker nobody runs, so
this script measures first: it runs pyright, compares the error count with
``pyright_baseline.json`` and fails only when the count grows. New findings fail;
existing ones stay visible in the printed breakdown until someone pays them down.

    python scripts/check_types.py                 # compare against the baseline
    python scripts/check_types.py --write-baseline  # record a deliberate new level

Exit codes: 0 = at or below baseline, 1 = regression (or worse: the tool failed),
2 = type check NOT EXECUTED (pyright/npx unavailable), which CI must not read as a
pass. Bumping the recorded pyright version is a deliberate act: a newer checker can
report more, and the baseline has to be re-measured, not silently relaxed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parents[1]
DEFAULT_BASELINE = ROOT / "pyright_baseline.json"
DEFAULT_COMMAND = ("npx", "--yes", "pyright", "--outputjson")


def load_baseline(path: Path) -> dict:
    if not path.is_file():
        return {"errors": None, "rule_counts": {}, "tool_version": None}
    return json.loads(path.read_text(encoding="utf-8"))


def parse_pyright_report(report: dict) -> dict:
    """Count errors and group them for the printed breakdown."""
    rules: Counter[str] = Counter()
    files: Counter[str] = Counter()
    for diagnostic in report.get("generalDiagnostics") or []:
        if diagnostic.get("severity") != "error":
            continue
        rules[str(diagnostic.get("rule") or "(no rule)")] += 1
        files[Path(str(diagnostic.get("file") or "?")).as_posix()] += 1
    summary = report.get("summary") or {}
    return {
        "errors": int(summary.get("errorCount") or 0),
        "files_analyzed": int(summary.get("filesAnalyzed") or 0),
        "rule_counts": dict(rules.most_common()),
        "file_counts": files,
    }


def compare(baseline_errors: int | None, measured_errors: int) -> str:
    if baseline_errors is None:
        return "unbaselined"
    if measured_errors > baseline_errors:
        return "worse"
    if measured_errors < baseline_errors:
        return "better"
    return "equal"


def resolve_command(command: tuple[str, ...]) -> list[str] | None:
    """Resolve an executable, honouring Windows ``.cmd`` shims such as npx."""
    executable = shutil.which(command[0])
    if executable is None:
        return None
    argv = [executable, *command[1:]]
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c", *argv]
    return argv


def run_pyright(command: tuple[str, ...], cwd: Path) -> dict | None:
    argv = resolve_command(command)
    if argv is None:
        print(f"type check NOT EXECUTED: {command[0]!r} is not on PATH")
        return None
    completed = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    stdout = completed.stdout.strip()
    if not stdout.startswith("{"):
        print("type check NOT EXECUTED: pyright did not return a JSON report")
        print((completed.stderr or stdout)[:2000])
        return None
    return json.loads(stdout)


def tool_version(report: dict) -> str | None:
    return ((report.get("summary") or {}).get("version")) or None


def ask_tool_version(command: tuple[str, ...], cwd: Path) -> str | None:
    """pyright only reports its version in some releases; ask it directly."""
    without_report = tuple(
        part for part in command if not part.startswith("--output")
    )
    argv = resolve_command((*without_report, "--version"))
    if argv is None:
        return None
    try:
        completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    except OSError:
        return None
    first = (completed.stdout or completed.stderr or "").strip().splitlines()
    return first[0].strip() if first else None


def print_breakdown(measured: dict, *, limit: int = 8) -> None:
    print(f"files analysed: {measured['files_analyzed']}")
    print("errors by rule:")
    for rule, count in list(measured["rule_counts"].items())[:limit]:
        print(f"  {count:4d}  {rule}")
    print("worst files:")
    for path, count in measured["file_counts"].most_common(limit):
        try:
            shown = Path(path).resolve().relative_to(ROOT).as_posix()
        except ValueError:
            shown = path
        print(f"  {count:4d}  {shown}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--report", type=Path, help="read an existing pyright JSON report")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument(
        "--command",
        default=" ".join(DEFAULT_COMMAND),
        help="pyright invocation, e.g. 'npx --yes pyright --outputjson'",
    )
    args = parser.parse_args(argv)

    if args.report is not None:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    else:
        report = run_pyright(tuple(args.command.split()), ROOT)
    if report is None:
        return 2

    measured = parse_pyright_report(report)
    baseline = load_baseline(args.baseline)
    recorded = baseline.get("errors")
    version = tool_version(report) or (
        ask_tool_version(tuple(args.command.split()), ROOT) if args.report is None else None
    )

    if args.write_baseline:
        payload = {
            "errors": measured["errors"],
            "rule_counts": measured["rule_counts"],
            "tool_version": version or baseline.get("tool_version"),
            "scope": "pyrightconfig.json include",
            "measured": datetime.now(timezone.utc).date().isoformat(),
            "note": (
                "Report-mode baseline: these findings predate any type checking and the "
                "count depends on pyrightconfig.json plus the pyright version. "
                "scripts/check_types.py fails only when the count grows."
            ),
        }
        args.baseline.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"baseline written: {args.baseline} errors={measured['errors']}")
        return 0

    print(f"pyright version: {version or 'unknown'}")
    print_breakdown(measured)
    verdict = compare(recorded, measured["errors"])
    print(f"measured={measured['errors']} baseline={recorded} verdict={verdict}")

    if verdict == "worse":
        print("type errors increased; fix them or re-baseline deliberately")
        return 1
    if verdict == "better":
        print("fewer errors than the baseline: lower it with --write-baseline")
    if verdict == "unbaselined":
        print("no baseline recorded yet; run --write-baseline once, deliberately")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
