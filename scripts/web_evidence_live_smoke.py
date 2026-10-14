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
from datetime import datetime, timezone
from pathlib import Path

from app.ai.router import build_provider_router
from app.config import Settings
from app.core.orchestrator import Orchestrator
from app.search.crawl4ai_client import close_crawl4ai_client
from app.search.runtime import close_search_runtimes


GUIDE = Path(__file__).parents[1] / "docs/WEB_EVIDENCE_V2_LIVE_SMOKE_TEST.md"
DEFAULT_X_URL = "https://x.com/jack/status/20"


def load_questions(x_url: str) -> list[tuple[int, str]]:
    guide = GUIDE.read_text(encoding="utf-8")
    body = guide.split("## 2. Bộ 21 câu hỏi", 1)[1].split("## 3. Cách chấm", 1)[0]
    rows: list[tuple[int, str]] = []
    for line in body.splitlines():
        match = re.match(r"^(\d{1,2})\.\s+(.*)", line)
        if match:
            rows.append((int(match.group(1)), match.group(2).strip()))
        elif rows and line.strip() and not line.startswith("Nhóm "):
            num, previous = rows[-1]
            rows[-1] = (num, f"{previous} {line.strip()}")
    if [num for num, _ in rows] != list(range(1, 22)):
        raise ValueError("Smoke guide must contain exactly the numbered questions 1..21")
    rows[-1] = (
        21,
        f"Đọc URL X/Twitter status {x_url} bằng fetch_url mode=x_thread và tóm tắt "
        "nội dung; nếu không đọc được, nói rõ lỗi.",
    )
    return rows


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


async def run(args: argparse.Namespace, questions: list[tuple[int, str]]) -> int:
    modes = ("v1", "v2") if args.mode == "both" else (args.mode,)
    report: dict = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "method": "real Orchestrator.ask and configured AI providers; manual grading pending",
        "x_status_url": args.x_url,
        "quality_verdict": "NOT MEASURED",
        "results": [],
    }
    exit_code = 0
    for mode in modes:
        await close_search_runtimes()
        enabled = mode == "v2"
        settings = Settings(
            web_evidence_v2_enabled=enabled,
            web_auto_fetch_enabled=enabled,
        )
        router = build_provider_router(settings)
        if not router.configured_provider_names(False):
            raise RuntimeError("No usable text model: check provider keys and model in .env")
        try:
            orchestrator = Orchestrator(settings, router)
            for num, question in questions:
                started = time.perf_counter()
                row = {"id": num, "mode": mode, "question": question}
                try:
                    answer = await asyncio.wait_for(orchestrator.ask(question), args.timeout)
                    row.update({
                        "status": "completed",
                        "answer": answer.text,
                        "provider": answer.provider,
                        "searched": answer.searched,
                        "sources": answer.sources,
                        "images": answer.images,
                        "fallbacks": answer.fallbacks,
                    })
                except Exception as exc:
                    row.update({"status": "error", "error_type": type(exc).__name__})
                    exit_code = 1
                row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
                report["results"].append(row)
                _write(args.output, report)
                print(
                    f"{mode} {num:02d}/21: {row['status']} "
                    f"({row['elapsed_seconds']}s)",
                    flush=True,
                )
        finally:
            for provider in router.providers:
                await provider.aclose()
    await close_search_runtimes()
    await close_crawl4ai_client()
    for mode in modes:
        times = [r["elapsed_seconds"] for r in report["results"] if r["mode"] == mode]
        report[f"{mode}_summary"] = {
            "completed": sum(
                r["status"] == "completed"
                for r in report["results"]
                if r["mode"] == mode
            ),
            "attempted": len(times),
            "latency_median_sec": statistics.median(times) if times else None,
            "latency_p95_sec": sorted(times)[math.ceil(len(times) * 0.95) - 1] if times else None,
        }
    _write(args.output, report)
    print(f"Results saved to {args.output} (quality review still required)")
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("v1", "v2", "both"), default="both")
    parser.add_argument("--x-url", default=DEFAULT_X_URL)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(tempfile.gettempdir()) / "web_evidence_live_smoke.json",
    )
    parser.add_argument(
        "--list", action="store_true", help="show questions without calling providers"
    )
    args = parser.parse_args()
    questions = load_questions(args.x_url)
    if args.list:
        for num, question in questions:
            print(f"{num}. {question}")
        return 0
    if not Path(".env").is_file():
        parser.error(".env is missing; configure provider keys before the live run")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return asyncio.run(run(args, questions))


if __name__ == "__main__":
    raise SystemExit(main())
