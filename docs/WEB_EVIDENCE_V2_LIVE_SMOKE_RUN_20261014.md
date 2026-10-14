# Web Evidence V2 — local live smoke run (2026-10-14)

**Status: NO-GO for enabling V2.** This run used real configured model providers and
`Orchestrator.ask` on Windows, with the same 21 questions for each mode. It was run
without Telegram polling. It does not establish production quality or model accuracy.

## Reproduction

From the repository root, with a local `.env` containing provider configuration:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python -m scripts.web_evidence_live_smoke --mode both --timeout 90 --output "$env:TEMP\web_evidence_live_smoke_20261014.json"
```

The command exited **1** because V2 question 15 timed out. The JSON contains the
individual answers, sources, search usage, and timings; review these locally before
assigning accuracy or citation scores. The `.env` is ignored by Git and should never
be committed.

| Observed measure | V1 (flags off) | V2 (both flags on) |
| --- | ---: | ---: |
| Questions attempted | 21 | 21 |
| Calls completed without Python exception | 21 | 20 |
| Timed out | 0 | 1 (question 15, 90 s) |
| Calls reporting search use | 11 | 12 |
| Calls with nonempty source metadata | 11 | 12 |
| Median elapsed | 15.392 s | 18.744 s |
| p95 elapsed | 58.920 s | 74.232 s |

The observed V2 p95 was approximately **26% higher** than V1, just beyond the
25% ceiling in the acceptance plan. This is a single sequential run with intermittent
provider/backend errors and is **not** a reliable production latency estimate.

## Environment and limitations

- Both feature flags are **false** when reading the user's local `.env` through
  `Settings()`; the runner overrides them in memory for each mode, without editing
  `.env` or starting the Telegram bot.
- The host runs Windows, while the configured SearXNG and Crawl4AI hostnames target
  the private Docker network. Live logs reported SearXNG `ConnectError`, DDGS
  `DDGSException`, Crawl4AI network failures followed by reader fallback, and an
  intermittent model-provider HTTP 503. This limits what the run says about a
  properly configured Docker deployment.
- Answer accuracy, citation support, unsupported-claim rate, and the 18/21 quality
  gate are **NOT MEASURED**: a completed `ask()` call is not a correct answer.
- No Telegram polling, merge, push, VPS deployment, or production rollout was
  performed in this run.

## Independent checks

- `python tests/run_tests.py`: exit 0, **745 tests OK (11 skipped)**, 173.749 s.
- `python -m ruff check .`: exit 0.
- `python scripts/check_markdown_links.py`: exit 0.
- `python -m scripts.check_canonical_env_vocabulary`: exit 0.
- `git diff --check`: exit 0.
- GitNexus graph re-index completed with 4,021 nodes and 9,350 edges, but
  full-text search index creation failed; `analyze --repair-fts` also exited 1.
  GitNexus flow enumeration reported truncation, so absence of an impact is not
  proof of safety.

## Remaining GO gate

1. Run inside a correctly configured network where SearXNG is reachable, and
   validate Crawl4AI or its expected fallback. Avoid two Telegram polling instances
   using the same bot token.
2. Re-run the V1/V2 comparison with a stable provider and review the JSON answers
   against real sources, including groups B, D, E, and the direct URL/image/X paths.
3. Require at least 18/21 satisfactory V2 answers, no drop from V1, no important
   runtime/security regressions, and all automated tests green before merging or
   switching production flags on. Keep both flags off for rollback.
