"""Run the documented V1/V2 questions against configured real model providers.

This intentionally bypasses Telegram polling and records unscored answers for
independent source/citation review. It never edits .env or feature flags on disk.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import statistics
import tempfile
import time
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

from app.ai.router import build_provider_router
from app.config import Settings
from app.core.orchestrator import Orchestrator
from app.search.crawl4ai_client import close_crawl4ai_client
from app.search.runtime import close_search_runtimes


GUIDE = Path(__file__).parents[1] / "docs/WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md"
DEFAULT_X_URL = "https://x.com/jack/status/20"

# Category slugs for the standard 21-question guide, in group order.
_GUIDE_CATEGORIES = {
    "A": "basic",
    "B": "buried",
    "C": "multi_source",
    "D": "fresh",
    "E": "conflicting",
    "F": "url_direct",
}

_CITED_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

_TOKEN_NOTE = (
    "NOT REPORTED BY PROVIDER: the request returned no usage payload, so token "
    "counts are unavailable for this row."
)


def _usage_medians(rows: list[dict], key: str) -> float | None:
    counts = [
        row["provider_token_usage"][key]
        for row in rows
        if isinstance(row.get("provider_token_usage"), dict)
        and isinstance(row["provider_token_usage"].get(key), int)
    ]
    return statistics.median(counts) if counts else None


def load_questions(x_url: str) -> list[tuple[int, str]]:
    guide = GUIDE.read_text(encoding="utf-8")
    body = guide.split("## 2. Bộ 21 câu hỏi", 1)[1].split("## 3. Cách chấm", 1)[0]
    rows: list[tuple[int, str]] = []
    in_group_heading = False
    for line in body.splitlines():
        stripped = line.strip()
        group_match = re.match(r"^Nhóm ([A-F])\b", stripped)
        if group_match:
            in_group_heading = True
            continue
        match = re.match(r"^(\d{1,2})\.\s+(.*)", stripped)
        if match:
            rows.append((int(match.group(1)), match.group(2).strip()))
            in_group_heading = False
        elif rows and stripped and not in_group_heading:
            num, previous = rows[-1]
            rows[-1] = (num, f"{previous} {stripped}")
    if [num for num, _ in rows] != list(range(1, 22)):
        raise ValueError("Smoke guide must contain exactly the numbered questions 1..21")
    rows = [
        (num, re.sub(r"\[(https?://[^\]]+)\]\(\1\)", r"\1", question))
        for num, question in rows
    ]
    rows[-1] = (
        21,
        f"Đọc URL X/Twitter status {x_url} bằng fetch_url mode=x_thread và tóm tắt "
        "nội dung; nếu không đọc được, nói rõ lỗi.",
    )
    return rows


def load_questions_json(path: Path) -> list[tuple[int, str]]:
    """Load a reusable set of custom smoke questions with stable numeric IDs."""
    return [
        (record["id"], record["question"])
        for record in load_question_records(path)
    ]


def load_question_records(path: Path) -> list[dict]:
    """Load custom smoke questions with their category/rubric metadata."""
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("Question file must be a nonempty JSON array")
    records: list[dict] = []
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("Each question must be a JSON object")
        num, question = item.get("id"), item.get("question")
        if type(num) is not int or num < 1 or not isinstance(question, str) or not question.strip():
            raise ValueError("Each question needs a positive integer id and nonempty question")
        records.append({
            "id": num,
            "question": question.strip(),
            "category": (item.get("category") or "").strip(),
            "rubric": (item.get("rubric") or "").strip(),
        })
    if len(set(record["id"] for record in records)) != len(records):
        raise ValueError("Question IDs must be unique")
    return records


def guide_question_records(x_url: str) -> list[dict]:
    """The standard 21-guide questions with their group-derived categories."""
    ids_and_questions = load_questions(x_url)
    groups = _guide_group_for_id()
    return [
        {
            "id": num,
            "question": question,
            "category": _GUIDE_CATEGORIES.get(
                groups.get(num, "F"), "url_direct"
            ),
            "rubric": "",
        }
        for num, question in ids_and_questions
    ]


def _guide_group_for_id() -> dict[int, str]:
    """Map each numbered guide question to its A..F group letter."""
    guide = GUIDE.read_text(encoding="utf-8")
    body = guide.split("## 2. Bộ 21 câu hỏi", 1)[1].split("## 3. Cách chấm", 1)[0]
    mapping: dict[int, str] = {}
    current = "F"
    for line in body.splitlines():
        stripped = line.strip()
        group_match = re.match(r"^Nhóm ([A-F])\b", stripped)
        if group_match:
            current = group_match.group(1)
            continue
        match = re.match(r"^(\d{1,2})\.\s+", stripped)
        if match:
            mapping[int(match.group(1))] = current
    return mapping


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _cited_urls(answer_text: str) -> list[str]:
    return _CITED_URL_RE.findall(answer_text or "")


async def run(args: argparse.Namespace, records: list[dict]) -> int:
    modes = ("v1", "v2") if args.mode == "both" else (args.mode,)
    report: dict = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "method": "real Orchestrator.ask and configured AI providers; manual grading pending",
        "question_ids": [record["id"] for record in records],
        "x_status_url": args.x_url,
        "v2_auto_fetch_enabled": not args.no_auto_fetch,
        "quality_verdict": "NOT MEASURED",
        "token_usage_note": _TOKEN_NOTE,
        "results": [],
    }
    exit_code = 0
    for mode in modes:
        await close_search_runtimes()
        enabled = mode == "v2"
        settings = Settings(
            web_evidence_v2_enabled=enabled,
            web_auto_fetch_enabled=enabled and not args.no_auto_fetch,
        )
        router = build_provider_router(settings)
        if not router.configured_provider_names(False):
            raise RuntimeError("No usable text model: check provider keys and model in .env")
        configured_models = sorted({provider.model for provider in router.providers})
        try:
            orchestrator = Orchestrator(settings, router)
            guard_spies: dict[str, list[str]] = {}
            for record in records:
                num, question = record["id"], record["question"]
                started = time.perf_counter()
                spy_key = f"{mode}:{num}"

                # Audit hook: capture the exact URL set the deterministic
                # guard compares against. Patch the module-global name the
                # ask() body resolves, so production code stays untouched.
                import app.core.orchestrator as orchestrator_module

                real_enforce = orchestrator_module.enforce_v2_citations

                def spy_enforce(
                    text, allowed_urls, *, _spy=spy_key, _log=guard_spies, _real=real_enforce
                ):
                    _log[_spy] = sorted(str(url) for url in (list(allowed_urls) or []))
                    return _real(text, allowed_urls)

                row = {
                    "id": num,
                    "mode": mode,
                    "category": record["category"],
                    "question": question,
                    "web_evidence_v2_enabled": enabled,
                    "web_auto_fetch_enabled": mode == "v2" and not args.no_auto_fetch,
                    "provider_models_configured": configured_models,
                }
                try:
                    with unittest.mock.patch.object(
                        orchestrator_module, "enforce_v2_citations", spy_enforce
                    ):
                        answer = await asyncio.wait_for(
                            orchestrator.ask(question), args.timeout
                        )
                    diagnostics = answer.citation_diagnostics
                    # V1 never invokes the guard: an absent key stays None.
                    row["guard_allowed_urls"] = guard_spies.get(spy_key)  # None for V1
                    row.update({
                        "status": "completed",
                        "provider": answer.provider,
                        "timeout_seconds": args.timeout,
                        "answer": answer.text,
                        "cited_urls": _cited_urls(answer.text),
                        "searched": answer.searched,
                        "sources": answer.sources,
                        "source_urls": [
                            str(item.get("url") or "")
                            for item in answer.sources
                            if isinstance(item, dict)
                        ],
                        "images_count": len(answer.images),
                        "fallbacks": answer.fallbacks,
                        "citation_diagnostics": (
                            diagnostics.as_dict()
                            if diagnostics is not None
                            else None  # V1 path never rewrites the answer
                        ),
                        "provider_token_usage": (
                            dict(answer.usage) if answer.usage else _TOKEN_NOTE
                        ),
                    })
                except asyncio.TimeoutError:
                    row.update({
                        "status": "timeout",
                        "error_type": "TimeoutError",
                        "error_message": f"orchestrator.ask exceeded {args.timeout}s",
                    })
                    exit_code = 1
                except Exception as exc:  # noqa: BLE001 - harness must record, not die
                    row.update({
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:2000],
                    })
                    exit_code = 1
                row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                report["results"].append(row)
                _write(args.output, report)
                print(
                    f"{mode} {num:02d}: {row['status']} "
                    f"({row['elapsed_seconds']}s)",
                    flush=True,
                )
        finally:
            for provider in router.providers:
                await provider.aclose()
    await close_search_runtimes()
    await close_crawl4ai_client()
    for mode in modes:
        rows = [r for r in report["results"] if r["mode"] == mode]
        times = [r["elapsed_seconds"] for r in rows]
        report[f"{mode}_summary"] = {
            "completed": sum(r["status"] == "completed" for r in rows),
            "attempted": len(times),
            "latency_median_sec": statistics.median(times) if times else None,
            "latency_p95_sec": sorted(times)[math.ceil(len(times) * 0.95) - 1] if times else None,
            # Median of the rows that reported usage; None when none did.
            "prompt_tokens_median": _usage_medians(rows, "prompt_tokens"),
            "completion_tokens_median": _usage_medians(rows, "completion_tokens"),
            "usage_reported_rows": sum(
                isinstance(r.get("provider_token_usage"), dict) for r in rows
            ),
        }
    _write(args.output, report)
    print(f"Results saved to {args.output} (quality review still required)")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("v1", "v2", "both"), default="both")
    parser.add_argument("--x-url", default=DEFAULT_X_URL)
    parser.add_argument(
        "--questions-file", type=Path, default=None,
        help="optional JSON array of custom questions (id, question, category, rubric)",
    )
    parser.add_argument(
        "--no-auto-fetch",
        action="store_true",
        help="compare V2 evidence/synthesis with automatic source reads disabled",
    )
    parser.add_argument(
        "--ids",
        default="",
        help="optional comma-separated question IDs (e.g. 5,6,8,15) for focused reruns",
    )
    parser.add_argument(
        "--timeout", type=float, default=180.0,
        help="per-question orchestrator timeout in seconds; recorded per row on success",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "web_evidence_live_smoke.json",
    )
    parser.add_argument(
        "--list", action="store_true", help="show questions without calling providers"
    )
    args = parser.parse_args()
    if args.questions_file:
        records = load_question_records(args.questions_file)
    else:
        records = guide_question_records(args.x_url)
    if args.ids:
        if not re.fullmatch(r"\d+(,\d+)*", args.ids):
            parser.error("--ids expects comma-separated numeric question IDs")
        chosen = [int(item) for item in args.ids.split(",")]
        if len(set(chosen)) != len(chosen) or not set(chosen) <= {r["id"] for r in records}:
            parser.error("--ids contains duplicates or unknown question IDs")
        selected = set(chosen)
        records = [record for record in records if record["id"] in selected]
    if args.list:
        for record in records:
            category = f" [{record['category']}]" if record["category"] else ""
            print(f"{record['id']}. {record['question']}{category}")
        return 0
    # Docker Compose injects .env via env_file without copying it into the image.
    # run() validates configured providers, so an on-disk .env is not required.
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return asyncio.run(run(args, records))


if __name__ == "__main__":
    raise SystemExit(main())
