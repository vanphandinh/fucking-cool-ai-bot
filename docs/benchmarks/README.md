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
python scripts/web_evidence_benchmark.py --output docs/benchmarks/web_evidence_v1_baseline.json
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
