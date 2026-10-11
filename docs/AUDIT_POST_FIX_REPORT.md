# Independent post-remediation repository audit — 2026-10-11

> **Scope:** local working tree on `codex/audit-remediation-2026-10-11`, baseline
> `8e3f691709f58567b36af3b791364d2c334f37e6` (`main`). No production
> inspection, deployment, credentials or VPS access. Findings are based on
> locally inspectable code, tests and GitNexus MCP; an untested exploit is
> explicitly distinguished from a demonstrated defect.

## A. Executive summary

The new audit started from the **entire 180-file Git-tracked inventory** (58
application files, 89 tests, 11 docs, 8 scripts, 1 CI workflow and 13 other
configuration/root files). All 180 were inspected for structure and readable
content; all 155 tracked Python files were parsed, all 3 TOML files parsed,
and entry points, inter-module dependencies and failure boundaries were traced
through selected deep source review and **GitNexus MCP**. The untracked audit
regression tests were also executed. The appendix contains the explicit
tracked-file inventory; inventory inspection does not imply that each file
received exhaustive manual line-by-line or dynamic security coverage.

Supplemental GitNexus MCP `detect_changes(scope="all")` reported **83 changed
symbols**, **38 affected symbols** and **24 modified tracked files**, with
`risk_level="critical"` for the integration blast radius. No `partial` or
`truncated` condition was reported. Delivery and provider-router HIGH-risk
call graphs were specifically considered in regression selection.

**Independent residual findings: 0 CRITICAL, 0 HIGH, 0 MEDIUM, 1 LOW, 1 INFO.**
LOW is an observed legacy per-topic lock-retention issue. INFO is the
**unverified** Crawl4AI fetch-boundary/SSRF hypothesis. No secret leakage,
remote SSRF exploitation, production memory exhaustion or vulnerable
production deployment was demonstrated. The GitNexus change-impact label
`critical` describes **regression blast radius**, not a security severity.

**Supplemental privacy follow-up:** NEW-03 (LOW) was subsequently reproduced
with a synthetic provider payload and **FIXED + VERIFIED**. It is not an
outstanding residual finding; the residual totals above are unchanged. The
original 582-test four-pass audit remains historical evidence; a 583-test
suite was run after the additional regression and fix (see §E).

The five mandatory remediation items **AUD-01 through AUD-05 are FIXED +
VERIFIED** by local source review and regression tests. **AUD-06 is UNVERIFIED**
within the permitted local investigation. Python 3.12 Docker tests passed
**582/582** initially, and **583/583** after NEW-03, on Docker Python 3.12.
The earlier Docker image smoke and dependency checks passed; Python 3.11 CI
and remote VPS checks were not executed. Some optional verification limits
remain explicit below.

**2026-10-12 AUD-04 follow-up:** the prior bounded-sweep solution proved
insufficient under steady churn and was replaced by an expiration heap with
lazy invalidation and bounded compaction. The new deterministic regressions
failed before the change and passed after it; the post-change **590-test**
Windows Python 3.13 suite passed (2 skips), as did a follow-up Docker
Python 3.12 suite (**590 tests, 0 skips**). The earlier Python 3.12 results
above predate this follow-up. See the updated AUD-04 disposition and §E.

### Architectural and trust-boundary map

`aiogram` message/callback -> allowlisted chat filter -> legacy question
handler or controlled JobManager -> Orchestrator -> AIProviderRouter ->
Chainnode/xKiro HTTP drivers -> tool executor -> SearXNG/DDGS, image search,
X URL resolver, generic HTTP reader and optional Crawl4AI service. Answer
delivery -> Telegram chunk acknowledgements -> ChatMemory/Stats -> job
snapshot and terminal status presenter. Configuration derives from Settings,
`.env` and the deployment's Compose profiles. Sensitive boundaries are chat
authorization, provider/tool text serialization, outbound URL fetch, Telegram
acknowledgement and cancellation, and diagnostic logging.

**2026-10-12 AUD-04 warning cooldown follow-up:** merge review exposed an
additional variable-cooldown regression (`10s` warning followed by a `90s`
check after 11 seconds, with intervening cleanup). The regression failed
before the patch. A 120-second bounded timestamp lookback now preserves the
comparison; a longer cooldown observed before expiry extends retention from
the original warning timestamp without sliding on denied calls. Once a short
warning has aged beyond the bounded lookback and has been removed, a newly
requested longer cooldown cannot recover its past timestamp. This is an
explicit finite-memory limitation for the otherwise unbounded cooldown API.
All **18 focused tests** pass; **593 tests** pass on Windows Python 3.13
(2 skipped) and offline Docker Python 3.12 (0 skipped). Warning churn
(100 distinct users/second for 400 seconds) retains 12,000 warning entries,
and idle cleanup removes them after 121 seconds. GitHub CI remains pending.

## B. Detailed findings and disposition

### NEW-01 — LOW — Legacy topic locks retain distinct keys indefinitely

- **Location/category:** `app/bot/handlers.py:151-162` (`_ChatLocks`),
  `app/bot/handlers.py:175`, `app/bot/handlers.py:405` (construction/use);
  resource retention / availability. **Confidence: high** for retention,
  **low** for materially harmful production resource exhaustion.
- **Root cause:** `_locks` is a strong-reference dictionary keyed by
  `(chat_id, topic_id)` (or chat ID). `get()` adds absent locks but never
  prunes keys, even after the async critical section releases the lock.
- **Reproduction:** execute a local `asyncio.run()` which instantiates
  `_ChatLocks`, awaits `get(1, topic)` for topics `0..2999`, then yields to the
  event loop; observed `len(obj._locks) == 3000` both before and after the
  yield. The test uses no Telegram or production network. This demonstrates
  cardinality retention, not a quantified memory pressure incident.
- **Exposure and impact:** accessible through allowlisted legacy bot chats;
  accumulation requires distinct chat/topic IDs over the router lifetime.
  Forum permissions and actual lifetime cardinality were not measured.
- **Recommendation:** bound *inactive* locks without separating concurrent
  waiters from a single key. A weak-value lock map can be considered, but must
  be tested under racing `get()` callers, waiters, cancellation and topic
  isolation; a naïve LRU eviction of locked/waited-on entries would break
  serialization. **Priority: P3.** Out of scope of the five mandated fixes.
- **Verification:** direct deterministic reproduction **PASS**;
  GitNexus MCP `impact(_ChatLocks, upstream)` **LOW**, 2 direct dependents;
  `context(_handle_question)` identified the legacy caller chain.

### NEW-02 / AUD-06 — INFO — Crawl4AI redirect/DNS egress policy unverified

- **Location/category:** `app/search/url_service.py:32,60-89`,
  `app/search/crawl4ai_client.py:122-198` (notably final URL validation
  `:186-192`), `docker-compose.yml:46-68`; trust boundary / suspected SSRF.
  **Confidence:** high that final URL validation occurs after the crawler
  fetch, **unknown** whether the pinned upstream service allows prohibited
  intermediate navigation.
- **Evidence/root concern:** the bot validates the submitted URL before
  calling the separate Crawl4AI server and checks its returned URL only
  after the service has fetched content. Compose publishes no crawler host
  port and disables JS/hooks, but contains no demonstrated per-hop IP-policy
  enforcement or egress firewall. These facts alone **do not prove SSRF**.
- **Reproduction status:** **UNVERIFIED**. Local
  `docker image inspect unclecode/crawl4ai:0.9.3` returned *No such image*;
  no isolated redirect/DNS-rebinding exercise was run. GitNexus MCP
  `explain(app/search/url_service.py)` had no persisted PDG/taint layer; a
  zero-finding result therefore does **not** establish safety. No internal
  IP, VPS or third-party endpoint was probed.
- **Potential consequence:** if the external crawler follows redirects or
  re-resolved domains to private/link-local destinations, post-fetch
  validation cannot undo an outbound request. Risk is conditional on a
  proven service behavior and its network routing.
- **Required isolated test:** run the **pinned** service in a disposable
  network containing a test public-facing redirect host and an internal
  request logger. Exercise public-to-private redirects, multi-hop chains,
  IPv4/IPv6 loopback/link-local/private ranges, DNS answer changes and
  allowed public URLs. Observe **whether the private destination actually
  receives a request**, including intermediate hops. If demonstrated,
  enforce resolved-IP/redirect restrictions before every browser/network
  request or deny private egress at the service network boundary; then retest
  public fetches and generic-reader fallback. **Priority: P2 investigation,
  conditional remediation.** Until verified, operators may set
  `CRAWL4AI_ENABLED=0`.

### NEW-03 — LOW — FIXED + VERIFIED — Untrusted AI tool name in diagnostics

- **Location/category:** `app/ai/drivers/openai_chat.py:149-159` (structured
  tool response parsing), `app/ai/router.py:484-495,512-515` (provider health
  and warning log), `app/ai/health.py:55,67,135` (last error storage).
  Diagnostic content injection / possible sensitive-content retention.
- **Evidence:** before the follow-up fix, the parser interpolated the
  unrecognized `function.name` supplied in a provider response into
  `ProviderError`. A local synthetic value containing the sentinel
  `PRIVACY-SENTINEL` and a newline was observed verbatim in the exception;
  the normal router error path passes `str(exc)` to health records and logs
  the exception. No real credential disclosure or production log compromise
  was demonstrated. **Confidence: high** for the unsafe data flow.
- **Impact:** an untrusted response could introduce arbitrary model-generated
  text and forged line breaks into provider diagnostic surfaces. The error
  was already a non-transient invalid-tool response, so the rejected tool is
  not executed.
- **Fix:** the structured parser now uses a constant diagnostic suffix for
  unknown tool names while keeping the provider identifier, non-transient
  recovery classification and existing allowlist enforcement. No untrusted
  name enters the exception. Regression:
  `tests/test_pr58_provider_payload_exception_privacy.py:15-68` exercises an
  HTTP mock with a synthetic marker and forged newline; it **failed before**
  the patch and **passed after**. Associated 23 provider/privacy tests passed.
  GitNexus MCP `impact(_parse_structured_tool_calls, upstream)` returned
  **LOW**, two affected functions (`parse_response` and `chat`).

### Mandatory original finding dispositions (not counted as new defects)

| ID | Severity before | Disposition and current evidence | Verification |
| --- | --- | --- | --- |
| AUD-01 | MEDIUM | **FIXED + VERIFIED:** `app/core/job_control.py`, `app/core/job_manager.py:225-295`, `app/bot/question_runner.py:125-255`, `app/bot/job_status.py`, `app/core/stats.py`. Confirmed Telegram prefix acknowledgement is tracked via `sent_parts/total_parts`; terminal `PARTIALLY_DELIVERED` records only acknowledged prefix with incomplete marker and an idempotent partial counter. Confirmed prefix has no automatic replay; controlled and legacy paths preserved. | `tests/test_audit_delivery_regressions.py`, delivery/manager/status, shutdown/callback and chronological history tests; Python 3.12 suite PASS. Transport ambiguity still prevents exactly-once network delivery. |
| AUD-02 | MEDIUM | **FIXED + VERIFIED:** `app/ai/router.py:54-70,205-245` converts tool exceptions into stable error categories instead of provider-visible `str(exc)`; cancellation and tool ordering preserved. Related raw logging at reader/legacy/image-result/startup boundaries sanitized. | `tests/test_audit_tool_error_privacy.py`, `tests/test_audit_reader_error_privacy.py`, `tests/test_audit_legacy_logging_privacy.py`; sentinel-leak and concurrency regressions PASS. |
| AUD-03 | LOW | **FIXED + VERIFIED:** `scripts/check_markdown_links.py:72-86` renders source paths using POSIX separators while resolving real paths with `Path`. | `tests/test_markdown_links.py`; link checker PASS on Windows and Python 3.12 image. |
| AUD-04 | LOW | **FIXED + VERIFIED locally (Windows Python 3.13 and Docker Python 3.12, 2026-10-12):** `app/core/rate_limiter.py` expires request and warning state with independent min-heap deadlines, lazy invalidation and compaction at `2 * live_expirations + 64`. Denied requests do not extend request retention. The 60-second sliding request window is unchanged. Warning history now has a bounded 120-second minimum lookback and may extend from the original warning time if a longer cooldown is requested while history remains. A cooldown increase first requested after the history is evicted cannot be reconstructed. | `tests/test_audit_retention_regressions.py`: **18 PASS**, including the initially failing variable-cooldown regression; 40,000-user request churn: 6,000 live keys then 1 after idle. Warning churn: 12,000 live history entries then 0 after idle. Full suite: Windows Python 3.13 **593 PASS (2 skipped)**; Docker Python 3.12 **593 PASS (0 skipped)**. Hosted CI and production verification pending. |
| AUD-05 | LOW | **FIXED + VERIFIED:** `app/core/context.py:14-130`, `app/config.py`, `app/main.py`, `.env.example`; 2048 conversation-key LRU and 24-hour inactivity TTL, preserving topic separation and ordered exchange semantics. | `tests/test_audit_retention_regressions.py`, `tests/test_controlled_memory_order.py` and topic isolation tests PASS. |
| AUD-06 | INFO | **UNVERIFIED** within local scope; see NEW-02. No speculative crawler-side patch applied. | Local code/Compose review PASS; isolated service navigation and network egress verification NOT RUN. |

## C. Prioritized action plan

1. **P2, security validation:** isolate and test Crawl4AI 0.9.3 redirect,
   DNS rebinding and egress before enabling optional rendering in production.
   Apply enforceable pre-request network policy **only if** the unsafe
   behavior is established; use `CRAWL4AI_ENABLED=0` as the interim switch.
2. **P3, availability:** resolve NEW-01 with concurrency-safe lock lifetime
   management and deterministic overlapping-topic/cancellation regression
   tests. Monitor distinct forum topics and RSS first to quantify impact.
3. **P3, release gates:** run GitHub Actions Python 3.11/3.12 matrix on a
   reviewed commit, then execute the documented remote canary/rollback
   handoff with separate deployment approval.

## D. Four-pass audit coverage

**Pass 1 — broad inventory:** inspected all 180 tracked paths, categories,
entry points (`app/main.py`, bot routers, AI drivers, search modules),
dependency pins/ranges, SearXNG settings, Docker, CI, docs and test suites.
Readable UTF-8 and parse checks passed for all relevant source classes.

**Pass 2 — deep paths:** traced Telegram auth/callback owner/status binding
(`app/bot/filters.py`, `app/bot/job_callbacks.py`,
`app/core/job_manager.py`), partial send/memory/order/metrics, legacy lock
management, cancellation/retry/shutdown, provider health and tool-result
privacy, HTTP/URL validation and Crawl4AI fallbacks, configs/secrets,
request/image limits, logging, resource lifetimes and test harness.
GitNexus MCP `query`, `context`, `impact`, and `explain` provided cross-module
call evidence. Its baseline index represents the original commit; unstaged
changes require `detect_changes`, not index freshness assumptions.

**Pass 3 — verification:** exercised 582 regression/integration tests on
supported Python 3.12, host Python 3.13 tests, controlled image smoke,
configuration/lint/link/dependency checks, and a deterministic 3,000-topic
lock-retention reproduction. Crawler redirect/DNS behavior was **not** tested
and no performance benchmark was claimed.
The supplemental NEW-03 check added one test and repeated the complete suite;
it did not constitute a new four-pass repository audit.

**Pass 4 — consistency:** compared revised code/docs, regression outcomes,
current source positions, baseline vs independent IDs, security-vs-blast-radius
severity, dependency advisory results, network/CI limits and affected call
paths. Avoided calling absent PDG findings SSRF proof or test coverage an
end-to-end VPS check.

**Coverage classification:** 180 tracked paths had **read-only structural
inspection**; the 89 tracked tests and 5 new audit tests were **executed**;
the 58 application files and 8 scripts had Python parse/lint coverage,
with deep dynamic/failure-injection review concentrated at the boundaries
above. Root/build/config/docs underwent the static and configuration checks
listed in §E. **Partial:** crawler container/network and GitNexus taint PDG;
**not checked:** actual VPS deployment, live Telegram/provider accounts,
GitHub-hosted CI Python 3.11. Untracked user-provided `AGENTS.md`,
`CLAUDE.md`, `.claude/` were left unmodified. The following appendix enumerates
the complete 180-file tracked baseline inventory.

## E. Verification results and limits

| Environment / command | Observed outcome | Notes |
| --- | --- | --- |
| **2026-10-12 AUD-04 warning cooldown follow-up** | **PASS: 18 focused tests; 593 full tests on each runtime** | Windows Python 3.13: 2 skipped, 43.600s. Docker Python 3.12 (`--network none`): 0 skipped, 27.321s. Earlier 15/590 rows below are historical runs before the three new regressions. 120-second bounded warning lookback and 150-second extension verified. |
| **2026-10-12 AUD-04: Windows Python 3.13 `python tests/run_tests.py`** | **PASS: 590 tests, 2 skipped, 47.170s** | Current AUD-04 heap patch and expanded regression tests. |
| **2026-10-12 AUD-04 focused retention tests** | **PASS: 15 tests** | Previously failed sustained-churn (23,800 vs 6,000), idle cleanup (445 vs 1), and denied-spam retention checks now pass; warning/heap/Telegram integration also covered. |
| **2026-10-12 AUD-04 Ruff, compileall, diff check** | **PASS** | Host Python 3.13 and current tracked code; rerun after documentation updates. |
| **2026-10-12 AUD-04 local no-network benchmarks** | **PASS: bounded state** | 100 new users/sec for 400s: 40,000 calls/0.116s, 6,000 live request keys; after idle: 1 key. Hot user: 20,000 accepted calls/0.033s, heap 2 entries for one active expiration. Denied spam: 200,000 calls/0.197s, one request state. Timings are observations, no CI thresholds. |
| **2026-10-12 AUD-04 Docker Python 3.12 post-change** | **PASS: 590 tests, 0 skipped, 28.593s** | Offline local Docker image `fcai-audit-tests-git:local` with bind-mounted current source. The initial run had one unrelated timing-sensitive failure: `test_nested_context_budget_is_charged_once_per_attempt` measured 20.103 ms against `<20 ms`; 9 focused `test_job_operations.py` tests subsequently PASS, and the repeated full suite PASS. All 15 AUD-04 focused tests also PASS in Docker. No out-of-scope code changed. |
| Windows Python 3.13.3 `python tests/run_tests.py` | **PASS:** 583 tests, 2 skipped, 49.023s | Supplemental full run after NEW-03; 3.13 not canonical CI. |
| Windows `python -m unittest discover -s tests -p 'test_*.py' -v` | **PASS:** 582 tests, 2 skipped | Original four-pass audit only; `%TEMP%/fcai-audit-discovery-final.log`. |
| Docker local Python 3.12 `python tests/run_tests.py` | **PASS:** 583 tests, 0 skipped, 30.244s | Supplemental full run with bind-mounted current source; image `fcai-audit-tests-git:local` digest `sha256:6e51529a5ce059c308ea196c5cb700161f744da5d4d65c384c22ee4ded04ac64`; isolated `--network none` with test-only `GIT_CONFIG_* safe.directory=/app`. |
| NEW-03 focused regression | **PASS:** 23 related provider/privacy tests | New synthetic unknown-tool-name test was **FAIL** before patch and **PASS** after; no real provider or credentials involved. |
| `python -m ruff check .` | **PASS** | Host, source plus regression tests. |
| `python -m compileall -q app tests scripts` | **PASS** | Host. |
| `python scripts/check_canonical_env_vocabulary.py` | **PASS** | Host. |
| `python scripts/check_markdown_links.py` | **PASS** | Host; re-run after report edits. |
| `git diff --check` | **PASS** | Tracked edits, re-run after reports. |
| Local Docker Compose `config --quiet` | **PASS** | Staged context using example environment copied to staging `.env`; no real credentials. Direct repo check without `.env` would fail for missing local file. |
| Docker 3.12 production-style `docker build --tag fcai-audit:postfix-local <staging>` | **PASS** (original remediation gate) | Resulting manifest `sha256:9562d3c015f273fe6caf0a2f13edea4d551692c1269e5f8a43072bcba19a82ae`. Build predates supplemental NEW-03 patch; rebuild release image after review. |
| Docker production image `Settings(_env_file=None)` smoke, `python -m pip check` | **PASS**, configuration valid; no broken requirements | Inert environment, no bot startup or VPS connection. |
| Ephemeral production-image `python -m pip_audit --progress-spinner off` | **PASS:** no known vulnerabilities | `pip-audit==2.10.1` installed into disposable container user site; advisory status is point-in-time, not an exploit assessment. |
| Forced offline `docker build --network none` | **BLOCKED by intentional network isolation** | Install layer needed PyPI (`pip>=26.2`); DNS unavailable. Subsequent regular local build PASS via cached layers. |
| First Docker Python 3.12 test pass | **Infrastructure failure, resolved** | Container Git considered staged worktree unsafe; test-only `GIT_CONFIG_* safe.directory=/workspace` permitted rerun to PASS. |
| First supplemental Docker Python 3.12 pass | **2 environment errors, resolved** | Host bind mount `/app` was not in container Git `safe.directory`; rerun with process-scoped `GIT_CONFIG_* safe.directory=/app` passed all 583 tests. |
| `python -m pip_audit` in the minimal Docker test image | **NOT AVAILABLE** | Module missing in test runner; separate production-image audit above PASS. |
| GitHub Actions `.github/workflows/audit.yml` (Python 3.11 and 3.12 on Ubuntu) | **NOT RUN** | No CI workflow was dispatched; 3.11 not locally available. |
| Crawl4AI isolated 0.9.3 SSRF navigation/egress tests | **NOT RUN / UNVERIFIED** | Container image absent locally; no real internal targets were probed. |
| Remote VPS canary, secrets, health and smoke | **NOT RUN — deployment not authorized** | Must be executed separately by operator after approval. |

The offline and CI tests establish local functional confidence; they cannot
prove exactly-once Telegram delivery under ambiguous network acknowledgement,
safe external crawler egress, or behavior in the live VPS configuration.

## Appendix — full tracked-file inventory at audit baseline

The file list below is generated from `git ls-files` on the audit branch;
existing tracked paths are enumerated regardless of whether modified in this
worktree. New untracked audit tests and the report itself are extra working
tree artifacts, not part of the 180-file baseline.

<!-- TRACKED_INVENTORY_START -->

### root/config/deployment (13 tracked paths)

- `.dockerignore`
- `.env.example`
- `.gitignore`
- `DEPLOY_SEARXNG_VPS.md`
- `Dockerfile`
- `README.md`
- `config/ai-providers.toml`
- `docker-compose.yml`
- `pyproject.toml`
- `requirements-audit.txt`
- `requirements.txt`
- `searxng/limiter.toml`
- `searxng/settings.example.yml`

### .github (1 tracked paths)

- `.github/workflows/audit.yml`

### app (58 tracked paths)

- `app/__init__.py`
- `app/ai/__init__.py`
- `app/ai/base.py`
- `app/ai/capabilities.py`
- `app/ai/catalog.py`
- `app/ai/contracts.py`
- `app/ai/drivers/__init__.py`
- `app/ai/drivers/base.py`
- `app/ai/drivers/openai_chat.py`
- `app/ai/drivers/registry.py`
- `app/ai/health.py`
- `app/ai/multimodal.py`
- `app/ai/provider.py`
- `app/ai/recovery.py`
- `app/ai/retry.py`
- `app/ai/router.py`
- `app/ai/runtime_config.py`
- `app/ai/scoped_health.py`
- `app/ai/synthesis.py`
- `app/ai/target.py`
- `app/ai/target_builder.py`
- `app/bot/__init__.py`
- `app/bot/controlled_handlers.py`
- `app/bot/filters.py`
- `app/bot/handlers.py`
- `app/bot/image_results.py`
- `app/bot/job_callbacks.py`
- `app/bot/job_status.py`
- `app/bot/media.py`
- `app/bot/question_runner.py`
- `app/config.py`
- `app/core/__init__.py`
- `app/core/context.py`
- `app/core/formatting.py`
- `app/core/job_control.py`
- `app/core/job_manager.py`
- `app/core/job_operations.py`
- `app/core/job_progress.py`
- `app/core/orchestrator.py`
- `app/core/rate_limiter.py`
- `app/core/request.py`
- `app/core/source_policy.py`
- `app/core/stats.py`
- `app/core/telegram_formatting.py`
- `app/core/thread_work.py`
- `app/main.py`
- `app/search/__init__.py`
- `app/search/crawl4ai_client.py`
- `app/search/ddgs_backend.py`
- `app/search/image_service.py`
- `app/search/reader.py`
- `app/search/resilience.py`
- `app/search/router.py`
- `app/search/runtime.py`
- `app/search/searxng_backend.py`
- `app/search/service.py`
- `app/search/url_service.py`
- `app/search/x_reader.py`

### scripts (8 tracked paths)

- `scripts/check_canonical_env_vocabulary.py`
- `scripts/check_markdown_links.py`
- `scripts/check_searxng_settings.py`
- `scripts/probe_chainnode.py`
- `scripts/probe_chainnode_vision.py`
- `scripts/probe_xkiro.py`
- `scripts/smoke_crawl4ai.py`
- `scripts/sync_env.py`

### tests (89 tracked paths)

- `tests/openai_target_fakes.py`
- `tests/provider_fakes.py`
- `tests/run_tests.py`
- `tests/test_ai_architecture_boundaries.py`
- `tests/test_ai_contracts.py`
- `tests/test_ai_transport_resilience.py`
- `tests/test_canonical_env_vocabulary.py`
- `tests/test_chainnode_audit.py`
- `tests/test_chainnode_probe.py`
- `tests/test_chainnode_provider.py`
- `tests/test_chainnode_vision_probe.py`
- `tests/test_config_regressions.py`
- `tests/test_controlled_memory_order.py`
- `tests/test_crawl4ai_client.py`
- `tests/test_crawl4ai_compose.py`
- `tests/test_forum_topic_isolation.py`
- `tests/test_free_routing.py`
- `tests/test_fresh_synthesis_routing.py`
- `tests/test_image_search.py`
- `tests/test_job_completed_status_cleanup.py`
- `tests/test_job_control.py`
- `tests/test_job_lifecycle.py`
- `tests/test_job_manager.py`
- `tests/test_job_operations.py`
- `tests/test_job_router.py`
- `tests/test_job_status.py`
- `tests/test_job_telegram.py`
- `tests/test_job_terminal_pruning.py`
- `tests/test_markdown_links.py`
- `tests/test_openai_chat_driver.py`
- `tests/test_pr58_catalog_numeric_validation.py`
- `tests/test_pr58_fresh_audit_regressions.py`
- `tests/test_pr58_provider_payload_exception_privacy.py`
- `tests/test_pr58_provider_shutdown_protocol.py`
- `tests/test_pr58_retry_state_machine_audit.py`
- `tests/test_pr58_transport_exception_privacy.py`
- `tests/test_privacy_and_job_regressions.py`
- `tests/test_provider_api_key_normalization.py`
- `tests/test_provider_catalog.py`
- `tests/test_provider_env_contract.py`
- `tests/test_provider_health_concurrency.py`
- `tests/test_provider_local_recovery_retry.py`
- `tests/test_provider_observability.py`
- `tests/test_provider_pool_config.py`
- `tests/test_provider_pool_factories.py`
- `tests/test_provider_portability.py`
- `tests/test_provider_recovery_policy.py`
- `tests/test_provider_recovery_scope_regressions.py`
- `tests/test_provider_registry.py`
- `tests/test_provider_retry_health.py`
- `tests/test_provider_retry_state.py`
- `tests/test_provider_routing.py`
- `tests/test_provider_runtime_regressions.py`
- `tests/test_provider_target_builder.py`
- `tests/test_provider_target_revalidation.py`
- `tests/test_provider_target_rotation.py`
- `tests/test_provider_target_security.py`
- `tests/test_provider_tool_capability_cache.py`
- `tests/test_provider_transport_retry.py`
- `tests/test_question_delivery.py`
- `tests/test_question_renewal_flow.py`
- `tests/test_router_reader_startup_regressions.py`
- `tests/test_scoped_provider_health.py`
- `tests/test_search_backend_auto.py`
- `tests/test_search_cancellation.py`
- `tests/test_search_policy_prompt.py`
- `tests/test_search_resilience.py`
- `tests/test_searxng_config.py`
- `tests/test_searxng_runtime_guard.py`
- `tests/test_source_dedupe.py`
- `tests/test_sync_env.py`
- `tests/test_sync_env_permissions.py`
- `tests/test_synthesis_context.py`
- `tests/test_telegram_delivery.py`
- `tests/test_telegram_formatting.py`
- `tests/test_thread_work.py`
- `tests/test_tool_batch_concurrency.py`
- `tests/test_tool_budget_synthesis.py`
- `tests/test_tool_fallback_recovery.py`
- `tests/test_tool_url_dedupe.py`
- `tests/test_url_service.py`
- `tests/test_url_tool_integration.py`
- `tests/test_vision.py`
- `tests/test_x_reader.py`
- `tests/test_x_reader_resilience.py`
- `tests/test_x_reader_response_limits.py`
- `tests/test_xkiro_probe.py`
- `tests/test_xkiro_provider.py`
- `tests/test_xkiro_vision_semantic_gate.py`

### docs (11 tracked paths)

- `docs/CHAINNODE.md`
- `docs/CRAWL4AI_INTEGRATION.md`
- `docs/ENV_SYNC.md`
- `docs/QUESTION_CONTROLS.md`
- `docs/README.md`
- `docs/SEARCH_RESILIENCE.md`
- `docs/TELEGRAM_FORMATTING.md`
- `docs/XKIRO.md`
- `docs/X_CONTENT_FETCHING.md`
- `docs/superpowers/specs/2026-09-18-generic-ai-provider-architecture-design.md`
- `docs/telegram-vision-input.md`
<!-- TRACKED_INVENTORY_END -->
