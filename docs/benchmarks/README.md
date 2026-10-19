# Web Evidence V2 offline benchmark

The initial V1 baseline is `archive/web_evidence_v1_baseline.json`. Its forty cases
are **synthetic**, checked-in snapshots produced by
`scripts/generate_web_evidence_fixtures.py`. No live website or personal data
is included. The dataset has 10 fact, 8 buried, 8 multi-source, 7 freshness,
and 7 unknown/conflicting cases. Freshness is evaluated against each case's
fixed `as_of` timestamp, not the current date.

Run from the repository root:

```sh
python -m unittest tests.test_web_evidence_benchmark -v
# 40-case replay set
python -m scripts.web_evidence_benchmark --config v2 --output /tmp/bench_v2_40.json
# 100-case replay set
python -m scripts.web_evidence_benchmark --config v2 --fixture tests/fixtures/web_evidence_v2/cases_100.json --output /tmp/bench_v2_100.json
# Auto-fetch path
python -m scripts.web_evidence_benchmark --auto-fetch --output /tmp/bench_autofetch.json
python -m scripts.generate_web_evidence_fixtures
```

The V1, chars-only and three-arm results below are **historical records** from
before the V2-only cutover. Their commands no longer exist; the JSON files live
in `docs/benchmarks/archive/`, are read-only, and are kept for audit — not as a
runnable baseline. The examples above write to `/tmp/` so a fresh run can never
overwrite them.

Two synthetic fixtures are checked in, both produced by
`scripts/generate_web_evidence_fixtures.py`:

| Fixture | Cases | Category counts | SHA-256 |
| --- | ---: | --- | --- |
| `cases.json` | 40 | 10 fact, 8 buried, 8 multi-source, 7 fresh, 7 unknown/conflicting | `1bf0e34571ea0360d06a348c8d930089e8174b5b60318a37060dab0c75c6323d` |
| `cases_100.json` | 100 | 30 fact, 20 buried, 20 multi-source, 15 fresh, 15 unknown/conflicting | `51e189382cad7efaeb245a75d9b11e9d1bb73e4077c1ac746b02f05012d9e399` |

The 40-case file is generated from the first entries of each group, so it stays
byte-identical while the 100-case file grows. Both files contain only synthetic
`*.example.*` snapshots and no live content.

The replay intercepts both search backends and URL HTTP reads, then exercises
the application's search routing, URL service, generic reader, and synthesis
packing. It uses a **fixed policy**: keep at most eight search results and read
the first two URLs. Real users' LLMs choose tools and URLs differently. Stage
latency is local fixture-processing time and cannot predict network or model
latency; p50 and p95 are calculated over cases, not over production traffic.
The token metric is a character/4 **estimate**, not provider token usage.
`source_recall_at_8` checks labeled source URLs among search results;
`evidence_recall` checks literal labeled evidence in successfully fetched page
outputs. These measures are distinct: a discoverable URL may yield no evidence.

`--config v2` is the only supported benchmark config: it packs with the V2 relevance/diversity policy, so a fresh 40-case run matches the PR4 `web_evidence_100_v2.json` semantics. The historical `archive/web_evidence_v2_pr2.json` / `archive/web_evidence_v2_pr3.json` files predate the V2-only cutover and their recorded numbers are kept unchanged. Historical arms A and B used the deleted V1 equal-split packer.

`answer_accuracy_extractive_proxy`, `citation_precision_extractive_proxy`,
and `unsupported_claim_rate_extractive_proxy` use a deterministic synthetic
`Dữ kiện:` extractor instead of model outputs. The proxy only produces claims
it has extracted, so high citation precision and a zero unsupported claim rate
are partly consequences of the judge design and **do not** establish model
quality. Conflicting or absent evidence needs independent human assessment.
Do not use these proxy scores as rollout acceptance criteria. PR4 requires
model predictions, human review of contested evidence, and a three-way
comparison (V1, expanded-character baseline, and adaptive V2).

`fixture_sha256` identifies the replay dataset. Repeated runs must match case
results and non-timing aggregate metrics; wall-clock latency naturally varies.
The benchmark must continue to run with network requests equal to zero.

## PR3 offline comparison (2026-10-14)

Same 40 snapshots (`fixture_sha256` agrees) and the same fixed first-two-source
fetch policy. PR3 adds lexical source ranking (title weight 2, snippet weight 1)
with canonical URL dedupe and domain interleaving, plus the `SEARCH_BACKEND=auto`
independence gate (two independent domains scoring at least 0.20). Ranking moves
the labeled source into the fetched prefix for the multi-source and
unknown/conflicting cases, so `evidence_recall` rises without any change to the
character caps.

| Measurement | V1 baseline | V2 PR3 | Difference |
| --- | ---: | ---: | ---: |
| Source recall@8 | 0.8627 | 0.8627 | 0.0000 |
| Evidence recall | 0.4894 | 0.7234 | +0.2340 |
| Extractive answer-accuracy **proxy** | 0.4750 | 0.6750 | +0.2000 |
| Extractive citation-precision **proxy** | 1.0000 | 1.0000 | 0.0000 |
| Extractive unsupported-claim-rate **proxy** | 0.0000 | 0.0000 | 0.0000 |
| Estimated median request tokens | 728 | 783.5 | +7.6% |
| Offline total-latency p50 / p95 | 627.935 / 764.749 ms | 538.873 / 587.226 ms | -14.2% / -23.2% |
| Mean fetches per case | 2 | 2 | 0 |

The ranking pass adds ~1.4 ms p50 to `search_ms` (0.372 → 1.735 ms), which is
negligible next to fetch time. The accuracy/citation numbers remain
**extractive proxy** results, not model answers; PR3 also cannot measure
auto-fetch orchestration, because the fixed replay never runs it; the separate
`--auto-fetch` replay measures that path.

## PR4 three-way comparison on the 100-case fixture (2026-10-14, historical pre-cutover)

All three arms run on the same 100 snapshots (`fixture_sha256`
`51e18938…d9e399`), the same fixed first-two-source fetch policy and the same
installed code. Only the documented knobs differ:

- **A. `--config v1`** — production V1: 8000-character reader output, 12000-character synthesis cap, no ranking.
- **B. `--config chars`** — V1 with only the character limits raised, simulated by patching `reader.MAX_CHARS=32000` and the synthesis cap to 32000. This is a comparison arm, **not** a shipped configuration.
- **C. `--config v2`** — bounded excerpt selection, source ranking, and relevance/diversity synthesis packing under the still-12000-character cap.

| Measurement | A. V1 | B. chars-only | C. V2 | C − A | C − B |
| --- | ---: | ---: | ---: | ---: | ---: |
| Source recall@8 | 0.8031 | 0.8031 | 0.8031 | 0.0000 | 0.0000 |
| Evidence recall (fetched output) | 0.4790 | 0.6134 | 0.6891 | +0.2101 | +0.0757 |
| Evidence recall (packed synthesis context) | 0.4790 | 0.4790 | 0.6891 | +0.2101 | +0.2101 |
| Labeled excerpts in the packed context | 0 | 0 | 169 | +169 | +169 |
| Non-web tool output preserved | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| Extractive answer-accuracy **proxy** | 0.4600 | 0.6200 | 0.6300 | +0.1700 | +0.0100 |
| Extractive citation-precision **proxy** | 1.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0000 |
| Extractive unsupported-claim-rate **proxy** | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| Estimated tokens, median | 744.5 | 744.5 | 794.0 | +6.6% | +6.6% |
| Estimated tokens, mean | 897.8 | 1164.5 | 995.5 | +10.9% | −14.5% |
| Estimated tokens, p90 | 1710.0 | 3377.0 | 2081.0 | +21.7% | −38.4% |
| Mean fetches per case | 2 | 2 | 2 | 0 | 0 |
| Offline total-latency p50 / p95 | 641.775 / 800.736 ms | 610.686 / 773.582 ms | 617.603 / 786.210 ms | −3.8% / −1.8% | +1.1% / +1.6% |

Per-category evidence recall shows where each arm acts:

| Category | A. V1 | B. chars-only | C. V2 |
| --- | ---: | ---: | ---: |
| fact (30) | 0.800 | 0.800 | 0.800 |
| buried (20) | 0.000 | 0.800 | 0.800 |
| multi-source (20) | 0.400 | 0.400 | 0.575 |
| fresh (15) | 0.800 | 0.800 | 0.800 |
| unknown/conflicting (15) | 0.357 | 0.357 | 0.500 |

Two honest readings of this table:

1. Raising the character limits alone recovers buried evidence in the *fetched*
text, but the **equal-split** synthesis packer then cuts those long outputs
again: `chars` scores 0.6134 fetched and 0.4790 in the context the model
actually receives. V2 reaches 0.6891 in the packed context and is the only arm
that keeps source labels (169 labeled excerpts total).
2. The `chars` arm's token median is unchanged only because 80% of cases are
short; its mean (+29.7%) and p90 (+97.5%) grow far more than V2's, i.e. it buys
its recall with a much larger tail. V2 gains evidence while staying inside the
unchanged 12000-character synthesis cap.

All accuracy/citation numbers remain **extractive proxy** measurements. They do
not establish model answer quality, human-verified citation precision,
auto-fetch orchestration behaviour, provider tokens or production latency; those
remain `NOT MEASURED` in [`../acceptance/web_evidence_v2_independent_validation_20261016/RELEASE_DECISION.md`](../acceptance/web_evidence_v2_independent_validation_20261016/RELEASE_DECISION.md).

## Auto-fetch offline replay (2026-10-14 hardening)

`--auto-fetch` is a second, separate replay: instead of a fixed first-two-source
policy it drives the real `Orchestrator.ask` with a mock router that issues one
`web_search`, a mock search backend returning the case's eight hits, and an
intercepted `reader._fetch_limited` that serves the case snapshots. The V2 policy is always on, so the request-scoped auto-fetch coordinator, URL service, reader
and packing all execute for real. Report:
`docs/benchmarks/web_evidence_auto_fetch.json`.

| Measurement (40 cases) | Value |
| --- | ---: |
| Mock page-read attempts (`network_requests`; real HTTP = 0) | 162 |
| Cases with an auto-fetch block | 23 / 40 |
| Auto-fetch attempts, mean / max | 2.025 / 4 |
| Auto-fetch sources actually read, mean / max | 0.575 / 2 |
| Peak read concurrency | 2 |
| Read success rate | 0.9 |
| Evidence delivery rate (expected phrases in the tool output) | 0.4681 |
| Tool output within 6,000 characters | 1.0 |
| Estimated request tokens, median | 526.0 |
| Offline elapsed p50 / p95 | 576.17 / 1107.73 ms |

The `network_requests` field in this auto-fetch JSON counts **intercepted mock
reader calls**, not external HTTP traffic. It is 90 here; the three 100-case
comparison reports separately record `network_requests = 0`.

The report carries machine-checked `invariants`: `max_two_sources`,
`attempts_within_ceiling`, `max_two_concurrent_reads` and `output_within_cap`
are all `true`. The evidence numbers are still **proxy/offline**: no model is
called and no live latency is measured, so `real_model_answer_accuracy`,
`human_checked_citation_precision`, `provider_request_tokens`,
`production_network_latency` and `live_rollout_safety` remain `NOT MEASURED`.

`auto_fetch_attempts` counts distinct URLs the burst tried to read, while
`auto_fetch_sources_read` counts only the attempts that returned content. The
two are separated because a failed read is replaced by the next ranked
candidate (2026-10-18): a dead link must not cost the question one of its two
evidence slots. Replacements share the same deadline and one claim set, and are
capped at twice the source limit, so attempts stay at or below four.

The synthetic fixtures only record a few readable pages per case, so most
search hits are intentionally unavailable; the replay therefore triggers
backfill far more often than production does. Compared with the previous
recorded run, evidence delivery rises 0.383 → 0.4681 because a replacement read
reaches the labeled page, while mock read attempts rise 90 → 162, median
estimated request tokens 435.5 → 526.0 (the caution header plus the extra read
evidence) and offline p95 doubles. These are replay-measured costs of the
backfill path, not production latency or token measurements.

## V1 token-estimate investigation: 728 -> 743

The historical V1 baseline reported `request_tokens_median_estimate` **728**;
after PR4 the same 40-case V1 arm reported **743**. This is a **benchmark
harness artifact, not a production regression**:

1. PR1/PR2 packed **three** outputs per case (the search listing plus the two
   page reads).
2. PR4 appended a fourth, synthetic **non-web** tool output
   (`NON_WEB_TOOL_OUTPUT`) so that non-web preservation could be measured. The
   V1 equal-split packer divides the body budget by the number of outputs, so a
   fourth output changes each share and the total packed length.
3. The V1 packer was deleted at the V2-only cutover; this section is kept only
   as a historical record of the old harness comparison.

Before the cutover, `--legacy-harness` reproduced the three-output shape
deterministically. The flag and the V1 arm were removed with the cutover, so
`docs/benchmarks/archive/web_evidence_v1_legacy_harness.json` remains the audit record
of that run: `request_tokens_median_estimate` 728.0, `evidence_recall` 0.4894,
`answer_accuracy_extractive_proxy` 0.475, `source_recall_at_8` 0.8627,
`citation_precision_extractive_proxy` 1.0,
`unsupported_claim_rate_extractive_proxy` 0.0. Current tests only assert the V2
pipeline, so these historical medians are no longer part of the live gate.

## PR2 offline comparison (2026-10-14)

Both reports use the **same 40 snapshots** (`fixture_sha256` agrees) and
fixed first-two-source fetch policy. PR2 selects bounded evidence from up to
120,000 characters of candidate text; it does **not** lift the 2 MiB download
limit, 6,000-character tool cap, or 12,000-character synthesis cap.

| Measurement | V1 baseline | V2 PR2 | Difference |
| --- | ---: | ---: | ---: |
| Source recall@8 | 0.8627 | 0.8627 | 0.0000 |
| Evidence recall | 0.4894 | 0.6383 | +0.1489 |
| Extractive answer-accuracy **proxy** | 0.4750 | 0.6500 | +0.1750 |
| Extractive citation-precision **proxy** | 1.0000 | 1.0000 | 0.0000 |
| Extractive unsupported-claim-rate **proxy** | 0.0000 | 0.0000 | 0.0000 |
| Estimated median request tokens | 728 | 782 | +7.4% |
| Offline total-latency p95 | 764.749 ms | 632.316 ms | -17.3% |
| Mean fetches per case | 2 | 2 | 0 |

The timing comparison is **not** controlled network/provider latency and
varies between runs; the 7.4% token change is estimated from characters.
The accuracy/citation/unsupported results are deterministic *extractive proxy*
numbers, not measured model answers or independent citation reviews. They
cannot satisfy rollout acceptance alone. PR3 source ranking, PR4 synthesis,
100-case three-way replay, human assessment, real-model evaluation, and
staged production monitoring remain outstanding.
