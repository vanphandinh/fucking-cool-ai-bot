# Web Evidence V2 offline benchmark

The initial V1 baseline is `web_evidence_v1_baseline.json`. Its forty cases
are **synthetic**, checked-in snapshots produced by
`scripts/generate_web_evidence_fixtures.py`. No live website or personal data
is included. The dataset has 10 fact, 8 buried, 8 multi-source, 7 freshness,
and 7 unknown/conflicting cases. Freshness is evaluated against each case's
fixed `as_of` timestamp, not the current date.

Run from the repository root:

```sh
python -m unittest tests.test_web_evidence_benchmark -v
python -m scripts.web_evidence_benchmark --output docs/benchmarks/web_evidence_v1_baseline.json
python -m scripts.web_evidence_benchmark --v2 --output docs/benchmarks/web_evidence_v2_pr3.json
```

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
conditional auto-fetch, because the replay never enables it (it needs both
`WEB_EVIDENCE_V2_ENABLED` and `WEB_AUTO_FETCH_ENABLED`).

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
