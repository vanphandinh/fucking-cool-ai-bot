# Web Evidence V2 — acceptance report (for independent review)

**Prepared:** 2026-10-14 (Asia/Bangkok).
**Branch:** `codex/web-evidence-v2`, repository `fucking-cool-ai-bot`.
**Purpose:** give an independent reviewer (ChatGPT) the raw artifacts needed to
decide whether Web Evidence V2 meets its quality, safety and acceptance gates.
This report does **not** claim project completion: production rollout has not
started and the answer-quality criteria that require a real model or human
grading are `NOT MEASURED`.

Legend used in section E: **PASS (offline)** = the criterion was met on the
synthetic offline fixture and still needs live confirmation; **NOT MEASURED** =
no valid evidence exists yet; nothing here is upgraded to `PASS`.

## A. Implementation status

| Stage | Status | Notes |
| --- | --- | --- |
| PR1 — offline baseline, 40 cases | DONE | commit `5f93dc8`; report `docs/benchmarks/web_evidence_v1_baseline.json` |
| PR2 — bounded extraction/chunking/selection | DONE | commit `9fcdd28`; report `docs/benchmarks/web_evidence_v2_pr2.json` |
| PR3 — source ranking + conditional auto-fetch | DONE (code, tests, offline gate) | commit `dbbf816`; report `docs/benchmarks/web_evidence_v2_pr3.json` |
| PR4 — synthesis packing, 100-case benchmark, docs | DONE (code, tests, offline benchmark, docs) | commit `bd06506`; reports `docs/benchmarks/web_evidence_100_{v1,chars,v2}.json` |
| PR4 — production rollout 5% → 25% → 100% + 7-day monitoring | **NOT STARTED** | no deployment, no live metrics |
| PR4 — acceptance on real model / human citation review | **BLOCKED** | no provider access in this environment, no human graders |

- **HEAD at the time of writing:** the commit that adds this report
  (`docs: add web evidence v2 acceptance report`), on top of `bd06506`.
- **Commit SHAs on the branch from `41d48c1`:** `5f93dc8` (PR1), `9fcdd28`
  (PR2), `dbbf816` (PR3), `bd06506` (PR4), then this report commit.
- **`git status` before the report commit:** clean except the untracked
  `.freebuff/` workspace directory created by the agent host (not committed).
- **Push / merge / deploy:** none. No branch was created, reset or rebased; no
  uncommitted work was overwritten.
- **Unfinished work:** everything in section G.

## B. Technical changes

### New modules

- `app/search/source_ranking.py` — lexical web ranking: accent/case-insensitive
  term extraction (reused from `app/search/evidence.py`), score
  `(2 × title coverage + snippet coverage) / 3`, canonical URL dedupe via
  `app/core/source_policy.py`, breadth-first domain interleave, and the
  independence gate (`has_independent_coverage`: ≥ 2 registrable domains with
  score ≥ 0.20). Never reads publication dates.
- `app/search/auto_fetch.py` — auto-fetch policy: `auto_fetch_active` (both
  flags required), `AutoFetchBudget` (question-scoped wall clock that never
  goes negative), `select_targets` (ranked, canonical, skips already-read and
  snippet-sufficient results), `pack_evidence` (hard cap, per-source URL +
  explicit read state, skip note reserved so it is never dropped).

### Modified modules

- `app/search/router.py` — `_meets_minimum` adds the V2 independence gate for
  `kind="web"`; SearXNG is accepted early only when the gate passes, otherwise
  DDGS top-up runs once and results are merged, deduped and ranked. The cache
  key is suffixed `:v2` for the gated path so V1 and V2 answers cannot mix.
  Image routing (`kind="image"`, `result_url_key="image_url"`) keeps the old
  count threshold.
- `app/core/orchestrator.py` — request-scoped `cached_read` helper (singleflight
  + canonical cache) shared by `fetch_url` and auto-fetch; `auto_fetch_evidence`
  reads at most `WEB_AUTO_FETCH_MAX_SOURCES` targets in parallel (≤ 2), each
  bounded by the remaining question budget via `asyncio.wait_for`, sharing one
  `AutoFetchBudget` per question and reusing the `operations` budget when the
  question runs as a controlled job. Failed or timed-out reads produce an
  honest `KHÔNG đọc được nguồn này` block, never a source entry or a `searched`
  flag. Auto-fetch is suppressed when the user message or quoted text contains
  a URL. A new `_request_urls` helper and `_TOOL_OUTPUT_CAP = 6000` mirror the
  router's per-tool truncation so appended evidence always fits.
- `app/ai/synthesis.py` — `_compact_tool_outputs` replaced the equal per-output
  split with labeled excerpt packing: web outputs are split on
  `[SOURCE n / CHUNK m]` / `[AUTO SOURCE]` labels, each block keeps its label,
  URL, `Heading` and read state, blocks are ranked by the last user message's
  lexical relevance then interleaved by source, a query with no terms preserves
  document order, and non-web tool outputs keep their own budget share
  (`WEB_EVIDENCE_BUDGET_SHARE = 0.7` for web) and their original position.
  Public signature unchanged; `max_evidence_chars` still defaults to 12,000.

### New/extended tests and fixtures

- `tests/test_web_evidence_source_ranking.py` (20 tests), `tests/test_web_auto_fetch.py`
  (16 tests), `tests/test_synthesis_evidence_packing.py` (11 tests);
  `tests/test_web_evidence_benchmark.py` extended to 11 tests covering both
  fixtures and the three replay arms.
- `tests/fixtures/web_evidence_v2/cases_100.json` (100 synthetic cases,
  SHA-256 `51e189382cad7efaeb245a75d9b11e9d1bb73e4077c1ac746b02f05012d9e399`);
  `cases.json` (40 cases) regenerates byte-identically
  (`1bf0e34571ea0360d06a348c8d930089e8174b5b60318a37060dab0c75c6323d`).
- `scripts/generate_web_evidence_fixtures.py` now generates both files from one
  entry list; `scripts/web_evidence_benchmark.py` gained `--config v1|chars|v2`,
  `--fixture`, packed-context recall, label counts, non-web preservation and
  token mean/p90 metrics.

### GitNexus results (graph, not grep)

- Before editing: `impact --direction upstream` — `route_auto` **HIGH**
  (2 direct dependents `search`, `search_images`; 4 processes; 3 modules),
  `_format_search_results` **HIGH** (2 direct; 3 processes; 3 modules);
  `read_url` and `ask` returned **UNKNOWN/ambiguous** when resolved by name, so
  they were treated as unresolved, not safe.
- `build_fresh_synthesis_messages` **CRITICAL** (26 impacted, 2 direct call
  sites in `app/ai/router.py`, 41 processes, 10 modules) and
  `_compact_tool_outputs` **CRITICAL** (1 direct, 41 processes).
- `detect-changes --scope all --repo .` before the PR3 commit: 7 files,
  26 symbols, 111 affected processes, risk **critical**, no `partial`/
  `truncated` flag. Before the PR4 commit: 9 files, 37 symbols, 121 affected
  processes, risk **critical**, no `partial`/`truncated` flag.
- **Warning acknowledged, not waived:** both commits are critical-risk because
  they touch central orchestration and synthesis symbols. `riskSharedAxes` was
  not used to dismiss it; the mitigation is that public signatures and the
  flag-off paths are unchanged and the full regression suite passes (section C).

### Safety, cache, timeout and rollback mechanics

- Unchanged and re-verified by tests: SSRF/DNS-pinning/redirect validation, the
  2 MiB download cap, request/URL/crawl timeouts, cancellation propagation,
  search cache + stale cache + circuit breaker + singleflight, Crawl4AI
  isolation, the 6,000-character tool cap and the 12,000-character synthesis
  cap.
- Added bounded resources: ≤ 2 auto-fetched sources, ≤ 2 concurrent reads, one
  ≤ 20 s question-scoped auto-fetch budget further bounded by the question/job
  budget, and evidence packed into the *remaining* tool-output room.
- Rollback: `WEB_EVIDENCE_V2_ENABLED=0` and/or `WEB_AUTO_FETCH_ENABLED=0`
  (both default `false`) restore V1 routing, ordering, reading, synthesis
  packing and cache keys. V1 and V2 cache entries are namespaced (`:v2`).

## C. Test results

Environment: Windows, Python 3.13 (the suite also runs on 3.11/3.12 in CI),
offline. Commands run from the repository root.

| Command | Result |
| --- | --- |
| `python tests/run_tests.py` (full discovery, after PR4) | **713 tests, OK (skipped=11)** |
| `python tests/run_tests.py` (after PR3) | 698 tests, OK (skipped=11) |
| `python -m ruff check .` | All checks passed |
| `python -m scripts.check_canonical_env_vocabulary` | passed |
| `python scripts/check_markdown_links.py` | passed (after this report was added) |
| `python -m unittest tests.test_web_evidence_source_ranking tests.test_web_auto_fetch` | 39 tests, OK |
| `python -m unittest tests.test_synthesis_evidence_packing` | 11 tests, OK |
| `python -m unittest tests.test_web_evidence_benchmark` | 11 tests, OK |
| `python -m unittest tests.test_synthesis_context tests.test_tool_budget_synthesis tests.test_fresh_synthesis_routing` | OK |
| `python -m unittest tests.test_reader_article_focus tests.test_url_service tests.test_tool_url_dedupe tests.test_web_evidence_v2 tests.test_search_backend_auto tests.test_search_resilience tests.test_search_cancellation tests.test_image_search tests.test_url_tool_integration` | 105 tests, OK |

Coverage added by PR3/PR4 (all as automated tests, not manual claims):

- **Unit/policy:** title-2 vs snippet-1 weighting, Vietnamese accent folding,
  canonical dedupe, domain interleave, `limit` behaviour, junk/empty URL
  handling, date fields ignored, threshold boundary.
- **Routing/fallback:** V2 gate passes with two independent relevant domains,
  top-up when SearXNG has one domain or only irrelevant results, DDGS error
  fallback, stale cache only after both backends fail, `:v2` cache isolation and
  rollback to V1 ordering, image routing unaffected by the web gate.
- **Auto-fetch:** both flags required, budget floor/exhaustion, target
  selection (weak, already-read, snippet-sufficient), honest failure status,
  request-scoped cache shared with `fetch_url`, user-URL suppression, no-web
  question fetches nothing, concurrency capped at 2, cancellation of the whole
  question cancels auto-fetch, controlled-job slot/budget path (real
  `OperationRunner` + `JobControl`), two concurrent questions keep separate
  caches and budgets.
- **Synthesis packing:** relevant excerpt kept while filler is dropped, deep
  excerpt preferred across sources, labels+URLs per excerpt, multi-source
  representation, non-web outputs preserved under huge web evidence, cap never
  exceeded for 600/1200/4000/12000, document order for summary queries,
  prompt-injection text stays inside the untrusted frame, inputs not mutated.
- **Benchmark contracts:** fixtures regenerate byte-identically, 100-case
  category counts (30/20/20/15/15), deep/fresh/conflict invariants, three arms
  deterministic and bounded, arm-specific limits (`chars` = 32000/32000,
  V1/V2 = 8000/12000), buried delivery differs across arms.

**Not run / not available in this environment:** no live network, provider or
Telegram test; CI workflow not executed here; no load or soak test; no
production smoke test; no browser/UI test (not applicable).

## D. Benchmark results

All numbers below are produced by
`scripts/web_evidence_benchmark.py`, which replaces search and HTTP with
synthetic snapshots while executing the real routing, URL service, reader and
synthesis packing code. Network requests: **0**.

- **Fixture:** `tests/fixtures/web_evidence_v2/cases_100.json`, 100 cases
  (30 fact, 20 multi-source, 20 buried, 15 fresh, 15 unknown/conflicting),
  SHA-256 `51e189382cad7efaeb245a75d9b11e9d1bb73e4077c1ac746b02f05012d9e399`.
  Historical 40-case set: `cases.json`, SHA-256
  `1bf0e34571ea0360d06a348c8d930089e8174b5b60318a37060dab0c75c6323d`.
- **Reports:** `docs/benchmarks/web_evidence_100_v1.json`,
  `web_evidence_100_chars.json`, `web_evidence_100_v2.json` (100 cases);
  `web_evidence_v1_baseline.json`, `web_evidence_v2_pr2.json`,
  `web_evidence_v2_pr3.json` (40 cases, retained).
- **Model/provider:** none. No LLM or provider endpoint is called; there is no
  model configuration to report. Latency is local fixture processing.
- **Evaluation method:** fixed policy — search eight results, fetch the first
  two, pack evidence; answer accuracy/citation precision/unsupported-claim rate
  come from a deterministic extractive judge over `Dữ kiện:` strings, i.e. a
  **proxy**, not model output.
- **Arms:** A = V1; B = V1 with only character limits raised (reader 8,000 →
  32,000 and synthesis 12,000 → 32,000, simulated by patching the constants —
  a comparison arm, not a shipped configuration); C = adaptive V2 with the
  production caps unchanged (8,000 reader behaviour replaced by bounded
  selection, 12,000 synthesis).

| Metric (100 cases) | A. V1 | B. chars-only | C. V2 | C − A |
| --- | ---: | ---: | ---: | ---: |
| Source recall@8 | 0.8031 | 0.8031 | 0.8031 | 0.0000 |
| Evidence recall (fetched text) | 0.4790 | 0.6134 | 0.6891 | +0.2101 |
| Evidence recall (packed synthesis context) | 0.4790 | 0.4790 | 0.6891 | +0.2101 |
| Labeled excerpts in packed context (total) | 0 | 0 | 169 | +169 |
| Non-web tool output preserved | 100% | 100% | 100% | 0 |
| Answer accuracy (extractive **proxy**) | 0.4600 | 0.6200 | 0.6300 | +0.1700 |
| Citation precision (extractive **proxy**) | 1.0000 | 1.0000 | 1.0000 | 0.0000 |
| Unsupported claim rate (extractive **proxy**) | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Estimated tokens, median | 744.5 | 744.5 | 794.0 | +6.6% |
| Estimated tokens, mean | 897.8 | 1164.5 | 995.5 | +10.9% |
| Estimated tokens, p90 | 1710.0 | 3377.0 | 2081.0 | +21.7% |
| Mean fetches per case | 2.0 | 2.0 | 2.0 | 0 |
| Offline total latency p50 / p95 (ms) | 641.775 / 800.736 | 610.686 / 773.582 | 617.603 / 786.210 | −3.8% / −1.8% |

Per-category evidence recall (fetched text): buried 0.000 / 0.800 / 0.800;
multi-source 0.400 / 0.400 / 0.575; unknown-conflicting 0.357 / 0.357 / 0.500;
fact 0.800 / 0.800 / 0.800; fresh 0.800 / 0.800 / 0.800.

Interpretation limits (must not be read as acceptance):

- The citation-precision 1.0 and unsupported-claim-rate 0.0 are partly
  consequences of the judge design (it only emits claims it extracted), so they
  are **not** evidence of real citation quality.
- Token numbers are `characters / 4` estimates, not provider usage. The ~±10%
  run-to-run variation in wall-clock latency means the latency column only
  shows that V2 does not add material local overhead.
- The `chars` arm's token *median* is unchanged because 80% of cases are short;
  its mean and p90 expose the real cost of buying recall with raw length, and
  its packed-context recall (0.4790) shows that fetching more text is not the
  same as delivering it.
- Conditional auto-fetch is **not exercised** by the replay (`web_auto_fetch`
  stays off; the replay calls the URL service directly), so its quality,
  latency and token effects are `NOT MEASURED`.
- 40-case PR3 replay for reference: evidence recall 0.4894 (V1) → 0.7234 (V2),
  extractive accuracy proxy 0.4750 → 0.6750, tokens 728 → 783.5.

## E. Acceptance verdict

| Criterion | Verdict | Evidence |
| --- | --- | --- |
| Answer accuracy ≥ +5 pp vs V1 | **NOT MEASURED** | requires real-model answers; the offline extractive proxy shows +17.0 pp (100 cases) but is judge-derived, not model output |
| Evidence recall ≥ +10 pp | **PASS (offline)** | 0.4790 → 0.6891 (+21.01 pp) on the frozen 100-case fixture; live-web confirmation still missing |
| Citation precision ≥ 95% | **NOT MEASURED** | only the extractive proxy (1.0) exists; no independent/human citation review was possible |
| Unsupported claim rate does not increase | **NOT MEASURED** | proxy 0.0 in both arms by construction; real-model behaviour untested |
| Latency p95 ≤ +25% | **PASS (offline)** | 786.210 ms vs 800.736 ms (−1.8%) on fixture processing; production network/model latency NOT MEASURED |
| Median tokens/request ≤ +30% | **PASS (offline)** | 794.0 vs 744.5 (+6.6%) estimated; provider tokens NOT MEASURED (mean +10.9%, p90 +21.7%) |
| All regression tests pass | **PASS** | `python tests/run_tests.py` → 713 OK, 11 skipped; `ruff check .` clean |
| Zero new security errors | **PASS (offline)** | no change to SSRF/DNS pinning/redirect/2 MiB/timeout/cancellation/Crawl4AI isolation; new tests cover flag-off paths, cancellation and bounded fetch; no external security review or live pen test performed |
| Feature-flag rollback works | **PASS (offline)** | flag-off tests assert V1 order/threshold/behaviour, no auto-fetch reads, and `:v2` cache isolation; operational rollback drill NOT MEASURED |

Overall: the offline technical gates for PR1–PR4 are met; **acceptance is not
achieved** because the model-quality criteria, the independent citation review
and the rollout evidence are missing.

## F. Rollout

- **Production deployment:** NOT STARTED. No stage of 5% / 25% / 100% has been
  started, no traffic has been routed to V2, and no metrics, dashboards or
  alerts exist for it.
- **Rollback drill:** NOT PERFORMED in a deployed environment. Only the
  flag-off behaviour is covered by automated tests.
- **Observed production days after 100%:** 0 (requirement is ≥ 7 days).
- **Operational blockers to starting rollout:** none identified in code, but a
  rollout needs (a) provider/model access to measure answer accuracy, (b) a
  human or independently scored citation sample, (c) an environment with real
  search/network latency, and (d) explicit operator authorization. None of these
  were available in this session.

## G. Outstanding issues and follow-ups

1. **Real-model evaluation** of arms A/B/C on the 100-case fixture (same model,
  same config) — required for the accuracy and unsupported-claim criteria.
2. **Independent citation review** for contested/conflicting cases and for
  abstain behaviour, plus a check that source URLs/timestamps remain correct.
3. **Live-web benchmark run** for network latency and freshness handling, with
  recorded timestamps for the fresh category.
4. **Auto-fetch measurement**: quality/latency/token effect of conditional
  auto-fetch is untested offline; needs a live replay that drives
  `Orchestrator.ask` with both flags on.
5. **Rollout + monitoring**: 5% → 25% → 100% with aggregate metrics, rollback
  drill, and ≥ 7 days of observation before any completion claim.
6. **Plan file retention**: `docs/WEB_EVIDENCE_V2_IMPLEMENTATION_PLAN.md` is
  intentionally kept; it may only be deleted after every section 7/8 condition
  is met, and only in a dedicated cleanup commit following
  `git log -- docs/WEB_EVIDENCE_V2_IMPLEMENTATION_PLAN.md`.
7. **Known limits, not defects:** the extractive judge's citation/unsupported
  numbers; token figures are estimates; `MAX_RESULTS=8`, 6,000/tool and
  12,000/synthesis remain untouched by design.

Đề nghị ChatGPT đánh giá độc lập dựa trên các commit, report và test evidence ở
trên; chưa tự tuyên bố toàn bộ nghiệm thu hoàn tất nếu vẫn còn FAIL/NOT MEASURED.
