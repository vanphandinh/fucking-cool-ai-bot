# Crawl4AI URL Reading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Crawl4AI 0.9.3 as the preferred private URL-rendering/extraction backend while preserving SearXNG/DDGS search routing, X/Twitter specialized fetching, SSRF protections, and the existing generic reader fallback.

**Architecture:** `web_search` is unchanged. `fetch_url` continues through `url_service.read_url()`: validate the URL, prefer the X-specific resolver for X status URLs, then try a new private Crawl4AI client for ordinary pages, and fall back once to the current generic reader on bounded Crawl4AI failure. Crawl4AI runs as an opt-in, version-pinned, non-public Docker service with bearer auth and no LLM provider credentials.

**Tech Stack:** Python 3.11+, asyncio, httpx, Pydantic Settings, aiogram, Docker Compose, Crawl4AI 0.9.3, unittest, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-09-crawl4ai-url-reading-design.md`

## Global Constraints

- Keep `app/search/router.py`, SearXNG primary routing, DDGS fallback, search cache, circuit breaker, singleflight, and source policy unchanged unless a regression test proves an integration-specific change is required.
- Crawl4AI is URL-reading infrastructure only; do not expose it as a new LLM tool and do not add a second LLM/provider configuration to Crawl4AI.
- Initial production image must be pinned to `unclecode/crawl4ai:0.9.3`; do not use `latest`.
- Do not publish Crawl4AI port `11235` to the host or Internet.
- Require a `CRAWL4AI_API_TOKEN` for Docker-network access and send it only as `Authorization: Bearer <token>`.
- Keep `CRAWL4AI_EXECUTE_JS_ENABLED=false` and `CRAWL4AI_HOOKS_ENABLED=false`.
- Preserve the bot-side `reader.validate_public_url()` check before every Crawl4AI request; Crawl4AI server-side SSRF validation is defense in depth, not a replacement.
- Never retry Crawl4AI more than once per `fetch_url` call; on a usable failure, fall back exactly once to the existing generic reader.
- `asyncio.CancelledError` must propagate and must not trigger fallback work.
- Do not remove or weaken the existing X/Twitter specialized reader.
- `CRAWL4AI_ENABLED=0` must provide an immediate no-redeploy application rollback.
- Completion requires full regression verification plus a final audit for security, timeout, cancellation, Docker exposure, secret leakage, and docs/config drift.

---

## File Map

**Create**
- `app/search/crawl4ai_client.py` — typed Crawl4AI HTTP client, payload construction, response normalization, output limiting, shared client lifecycle.
- `tests/test_crawl4ai_client.py` — isolated client contract/error/cancellation tests.
- `tests/test_crawl4ai_compose.py` — static regression checks for pinned image, private networking, token, disabled high-risk features, healthcheck.
- `scripts/smoke_crawl4ai.py` — opt-in live smoke test, never part of normal offline CI.
- `docs/CRAWL4AI_INTEGRATION.md` — deployment, config, rollback, troubleshooting, promotion policy.

**Modify**
- `app/config.py` — Crawl4AI settings and validation.
- `.env.example` — example configuration and rollback switch.
- `app/search/url_service.py` — route ordinary URL reads through Crawl4AI before generic fallback.
- `app/main.py` — startup warnings and shared Crawl4AI HTTP-client shutdown.
- `docker-compose.yml` — private opt-in Crawl4AI service.
- `tests/test_url_service.py` — routing/fallback/X precedence/SSRF regression tests.
- `tests/test_url_tool_integration.py` — preserve orchestrator semantics and per-question fetch cache.
- `tests/run_tests.py` only if this repository’s custom runner requires explicit registration for newly added tests; otherwise leave unchanged.
- `README.md` and `docs/SEARCH_RESILIENCE.md` — explain search-vs-reading responsibilities and operational profile.

---

### Task 1: Add configuration contract and rollback controls

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_url_service.py`

**Interfaces:**
- Produces settings:
  - `crawl4ai_enabled: bool`
  - `crawl4ai_url: str`
  - `crawl4ai_api_token: str`
  - `crawl4ai_timeout_sec: float`
  - `crawl4ai_max_chars: int`
- Later tasks consume those exact names from `Settings`.

- [ ] **Step 1: Write failing configuration tests**

Add tests asserting defaults and invalid timeout behavior:

```python
from app.config import Settings


def test_crawl4ai_defaults():
    settings = Settings(_env_file=None)
    assert settings.crawl4ai_enabled is True
    assert settings.crawl4ai_url == "http://crawl4ai:11235"
    assert settings.crawl4ai_timeout_sec == 25.0
    assert settings.crawl4ai_max_chars == 12000


def test_crawl4ai_timeout_must_be_below_question_timeout():
    with self.assertRaises(ValueError):
        Settings(
            _env_file=None,
            crawl4ai_timeout_sec=60,
            question_timeout_sec=30,
        )
```

- [ ] **Step 2: Run targeted tests and verify failure**

Run:

```bash
python -m unittest tests.test_url_service -v
```

Expected: FAIL because the Crawl4AI settings do not yet exist.

- [ ] **Step 3: Add minimal settings and validation**

In `Settings`, add:

```python
crawl4ai_enabled: bool = True
crawl4ai_url: str = "http://crawl4ai:11235"
crawl4ai_api_token: str = ""
crawl4ai_timeout_sec: float = Field(default=25.0, gt=0, allow_inf_nan=False)
crawl4ai_max_chars: int = Field(default=12000, ge=1000, le=50000)
```

Extend the existing model validator:

```python
if self.crawl4ai_timeout_sec >= self.question_timeout_sec:
    raise ValueError("CRAWL4AI_TIMEOUT_SEC phải nhỏ hơn QUESTION_TIMEOUT_SEC")
```

Do not require URL/token at model-validation time because missing credentials must degrade to the existing reader, not prevent bot startup.

- [ ] **Step 4: Add `.env.example` entries**

Under the web/search section, add:

```dotenv
# URL reader acceleration/rendering. 0 = instant rollback to existing generic reader.
CRAWL4AI_ENABLED=1
CRAWL4AI_URL=http://crawl4ai:11235
CRAWL4AI_API_TOKEN=
CRAWL4AI_TIMEOUT_SEC=25.0
CRAWL4AI_MAX_CHARS=12000
```

Explicitly document that the token must be a random secret and is shared only between bot and private Crawl4AI container.

- [ ] **Step 5: Run targeted tests**

Run:

```bash
python -m unittest tests.test_url_service -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/config.py .env.example tests/test_url_service.py
git commit -m "feat: add Crawl4AI reader configuration"
```

---

### Task 2: Implement the bounded authenticated Crawl4AI client

**Files:**
- Create: `app/search/crawl4ai_client.py`
- Create: `tests/test_crawl4ai_client.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class Crawl4AIReadResult:
    text: str
    source_url: str
    ok: bool
    reason: str = ""

async def read_page(url: str, settings: Settings) -> Crawl4AIReadResult
async def close_crawl4ai_client() -> None
```

- `url_service.py` consumes `read_page()` in Task 3.
- `main.py` consumes `close_crawl4ai_client()` in Task 5.

- [ ] **Step 1: Write failing success-contract tests**

Create tests using a mocked `httpx.AsyncClient` transport/client boundary. Cover these response shapes:

```json
{
  "success": true,
  "results": [
    {
      "success": true,
      "url": "https://example.org/article",
      "redirected_url": "https://example.org/article",
      "markdown": {
        "fit_markdown": "# Article\nUseful body",
        "raw_markdown": "# Article\nUseful body + nav"
      }
    }
  ]
}
```

Assert `fit_markdown` is selected first and the result backend-facing text is truncated to `settings.crawl4ai_max_chars`.

- [ ] **Step 2: Write failing defensive-parsing tests**

Add separate cases for:

```python
# raw fallback
{"markdown": {"fit_markdown": "", "raw_markdown": "raw body"}}

# malformed response
{"success": True, "results": "not-a-list"}

# crawl result failed
{"success": True, "results": [{"success": False, "error_message": "blocked"}]}

# no usable content
{"success": True, "results": [{"success": True, "markdown": {}}]}
```

Expected contract: no exception escapes for ordinary upstream failure; return `ok=False` with a short reason.

- [ ] **Step 3: Write authentication and secret-leak tests**

Assert the outgoing request contains:

```python
{"Authorization": f"Bearer {settings.crawl4ai_api_token}"}
```

and assert `result.reason` / exception-facing strings never contain the token value.

- [ ] **Step 4: Write cancellation test**

Mock the request to raise `asyncio.CancelledError` and assert it is re-raised:

```python
with self.assertRaises(asyncio.CancelledError):
    await read_page("https://example.org", settings)
```

- [ ] **Step 5: Run tests and verify they fail**

Run:

```bash
python -m unittest tests.test_crawl4ai_client -v
```

Expected: FAIL because module/interfaces do not exist.

- [ ] **Step 6: Implement the typed client**

Implement a process-local reusable `httpx.AsyncClient` with:

```python
httpx.AsyncClient(
    timeout=settings.crawl4ai_timeout_sec,
    trust_env=False,
)
```

The fixed server-authored request must be:

```python
payload = {
    "urls": [url],
    "browser_config": {},
    "crawler_config": {},
}
```

POST to:

```python
f"{settings.crawl4ai_url.rstrip('/')}/crawl"
```

Never accept model/user-provided browser config, hooks, JavaScript, provider, output path, or callback URL.

Parsing algorithm:

```python
results = body.get("results")
if not body.get("success") or not isinstance(results, list) or not results:
    return failed(...)
item = results[0]
if not isinstance(item, dict) or item.get("success") is False:
    return failed(...)
markdown = item.get("markdown")
if isinstance(markdown, dict):
    text = str(markdown.get("fit_markdown") or markdown.get("raw_markdown") or "")
elif isinstance(markdown, str):
    text = markdown
else:
    text = ""
```

Use `redirected_url` only after validating it with `reader.validate_public_url()`; otherwise preserve the original input URL as `source_url`.

Classify `401/403`, `429`, `5xx`, timeout, network error, malformed JSON, and empty content into short non-secret reasons. Do not retry.

- [ ] **Step 7: Implement shared client shutdown**

`close_crawl4ai_client()` must atomically detach and close the current client so repeated shutdown calls are harmless.

- [ ] **Step 8: Run targeted tests**

Run:

```bash
python -m unittest tests.test_crawl4ai_client -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add app/search/crawl4ai_client.py tests/test_crawl4ai_client.py
git commit -m "feat: add bounded Crawl4AI URL client"
```

---

### Task 3: Route `fetch_url` through Crawl4AI with exact fallback semantics

**Files:**
- Modify: `app/search/url_service.py`
- Modify: `tests/test_url_service.py`

**Interfaces:**
- Consumes `crawl4ai_client.read_page(url, settings)` from Task 2.
- Preserves the existing public interface exactly:

```python
async def read_url(url: str, settings: Settings, mode: str = "auto") -> UrlReadResult
```

- [ ] **Step 1: Add failing success-preference test**

For a normal page:

```python
crawl = AsyncMock(
    return_value=Crawl4AIReadResult(
        text="rendered markdown",
        source_url="https://example.org/article",
        ok=True,
    )
)
generic = AsyncMock(return_value="generic text")
```

Assert Crawl4AI is awaited once, generic reader is not awaited, result backend is `crawl4ai`, and `ok=True`.

- [ ] **Step 2: Add failing fallback matrix tests**

Use subtests for Crawl4AI returning `ok=False` with reasons corresponding to timeout/401/429/5xx/malformed/empty. Assert the generic reader is awaited exactly once and final backend remains `generic_reader`.

- [ ] **Step 3: Add feature/config bypass tests**

Cover:

```python
Settings(_env_file=None, crawl4ai_enabled=False)
Settings(_env_file=None, crawl4ai_enabled=True, crawl4ai_api_token="")
Settings(_env_file=None, crawl4ai_enabled=True, crawl4ai_url="")
```

Assert Crawl4AI is not called and generic reader handles the page.

- [ ] **Step 4: Add X precedence regression test**

Patch all three layers and assert an X status with successful specialized resolution calls neither Crawl4AI nor generic reader. Preserve current `mode=x_thread` behavior.

- [ ] **Step 5: Add SSRF-before-Crawl4AI regression test**

Call:

```python
await read_url("http://127.0.0.1/private", settings)
```

and assert neither Crawl4AI nor generic reader receives a network call.

- [ ] **Step 6: Add cancellation regression test**

Make Crawl4AI raise `asyncio.CancelledError`; assert `read_url()` propagates cancellation and generic reader is not awaited.

- [ ] **Step 7: Run tests and verify failure**

Run:

```bash
python -m unittest tests.test_url_service -v
```

Expected: new tests FAIL because `url_service` does not yet call Crawl4AI.

- [ ] **Step 8: Implement routing order**

After existing URL validation and X-specific logic:

```python
if (
    settings.crawl4ai_enabled
    and settings.crawl4ai_url.strip()
    and settings.crawl4ai_api_token.strip()
):
    crawl = await crawl4ai_client.read_page(fetch_url, settings)
    if crawl.ok:
        return UrlReadResult(crawl.text, crawl.source_url, "crawl4ai", True)
    logger.warning("Crawl4AI URL read failed (%s); fallback generic reader", crawl.reason)
```

Then execute the existing `reader.read_page()` exactly once.

Do not wrap the Crawl4AI await with `except BaseException`; cancellation must continue to propagate. Ordinary client failures should already be normalized by the client.

- [ ] **Step 9: Run targeted tests**

Run:

```bash
python -m unittest tests.test_url_service -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add app/search/url_service.py tests/test_url_service.py
git commit -m "feat: prefer Crawl4AI for URL reads"
```

---

### Task 4: Preserve orchestrator/tool semantics end-to-end

**Files:**
- Modify: `tests/test_url_tool_integration.py`
- Modify: `app/core/orchestrator.py` only if a failing regression requires it

**Interfaces:**
- `fetch_url` tool name/schema remains unchanged.
- `UrlReadResult.backend` may now be `crawl4ai`; no model-visible new tool is added.

- [ ] **Step 1: Add Crawl4AI-backed success test**

Return:

```python
UrlReadResult(
    "rendered body",
    "https://example.org/article",
    "crawl4ai",
    True,
)
```

and assert:

- `answer.searched is True`;
- one source with canonical URL is recorded;
- model tool payload includes rendered body;
- no Crawl4AI-specific implementation detail is exposed in the tool schema.

- [ ] **Step 2: Preserve per-question URL cache test**

Extend the existing fetch-twice test so the same ordinary URL is read only once even when the LLM calls `fetch_url` twice in one question.

- [ ] **Step 3: Run test**

```bash
python -m unittest tests.test_url_tool_integration -v
```

Expected: PASS without modifying `orchestrator.py`. If it fails, make the smallest behavior-preserving correction and rerun.

- [ ] **Step 4: Commit**

```bash
git add tests/test_url_tool_integration.py app/core/orchestrator.py
git commit -m "test: preserve fetch_url semantics with Crawl4AI"
```

If `app/core/orchestrator.py` was not changed, omit it from `git add`.

---

### Task 5: Add the private, pinned Crawl4AI Docker service and lifecycle wiring

**Files:**
- Modify: `docker-compose.yml`
- Modify: `app/main.py`
- Create: `tests/test_crawl4ai_compose.py`
- Modify: `.env.example`

**Interfaces:**
- Docker hostname: `crawl4ai`
- Internal port: `11235`
- Health endpoint: `/health`
- App shutdown calls `close_crawl4ai_client()`.

- [ ] **Step 1: Write failing static Compose tests**

Test the YAML text/config for all of these invariants:

```python
assert "unclecode/crawl4ai:0.9.3" in text
assert 'profiles: ["crawl4ai"]' in text
assert "11235:11235" not in text
assert "CRAWL4AI_API_TOKEN" in text
assert "CRAWL4AI_EXECUTE_JS_ENABLED" in text
assert "CRAWL4AI_HOOKS_ENABLED" in text
assert "/health" in text
```

Also assert there are no OpenAI/Gemini/Groq/Anthropic provider keys under the Crawl4AI service block.

- [ ] **Step 2: Run test and verify failure**

```bash
python -m unittest tests.test_crawl4ai_compose -v
```

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Add Compose service**

Add a service equivalent to:

```yaml
  crawl4ai:
    image: ${CRAWL4AI_IMAGE:-unclecode/crawl4ai:0.9.3}
    container_name: fcai-crawl4ai
    profiles: ["crawl4ai"]
    restart: unless-stopped
    init: true
    stop_grace_period: 30s
    shm_size: "${CRAWL4AI_SHM_SIZE:-512m}"
    environment:
      CRAWL4AI_API_TOKEN: ${CRAWL4AI_API_TOKEN:-}
      CRAWL4AI_EXECUTE_JS_ENABLED: "false"
      CRAWL4AI_HOOKS_ENABLED: "false"
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://127.0.0.1:11235/health"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 40s
```

Do not add `ports:`. Do not add a hard `depends_on` from `bot`; the app must remain able to start and fall back when Crawl4AI is absent.

- [ ] **Step 4: Wire shutdown and startup warning**

Import `close_crawl4ai_client` into `app/main.py` and close it during the same finally-chain that already closes search runtimes/providers.

Add warnings:

```python
if settings.crawl4ai_enabled and not settings.crawl4ai_url.strip():
    logger.warning("CRAWL4AI_ENABLED=1 nhưng CRAWL4AI_URL đang trống; dùng generic reader.")
if settings.crawl4ai_enabled and not settings.crawl4ai_api_token.strip():
    logger.warning("CRAWL4AI_ENABLED=1 nhưng thiếu CRAWL4AI_API_TOKEN; dùng generic reader.")
```

Do not fail startup.

- [ ] **Step 5: Add `.env.example` deployment knobs**

Add:

```dotenv
CRAWL4AI_IMAGE=unclecode/crawl4ai:0.9.3
CRAWL4AI_SHM_SIZE=512m
```

next to the Crawl4AI configuration.

- [ ] **Step 6: Run static tests**

```bash
python -m unittest tests.test_crawl4ai_compose -v
```

Expected: PASS.

- [ ] **Step 7: Validate Compose syntax**

Run:

```bash
docker compose config --quiet
```

Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml app/main.py .env.example tests/test_crawl4ai_compose.py
git commit -m "ops: add private Crawl4AI service"
```

---

### Task 6: Add live smoke test and operational documentation

**Files:**
- Create: `scripts/smoke_crawl4ai.py`
- Create: `docs/CRAWL4AI_INTEGRATION.md`
- Modify: `README.md`
- Modify: `docs/SEARCH_RESILIENCE.md`

**Interfaces:**
- Smoke script reads `CRAWL4AI_URL` and `CRAWL4AI_API_TOKEN` from environment.
- It must never run automatically in ordinary CI.

- [ ] **Step 1: Implement smoke script**

The script should:

1. GET `/health` with a short timeout;
2. POST `/crawl` for `https://example.com` using bearer auth;
3. assert a successful result contains non-empty Markdown;
4. optionally accept a second URL from `CRAWL4AI_SMOKE_JS_URL` for a JS-heavy target;
5. print timings and text lengths only, not response bodies or tokens;
6. exit non-zero on failure.

Use only dependencies already in `requirements.txt`, preferably `httpx` and `asyncio`.

- [ ] **Step 2: Add deployment documentation**

`docs/CRAWL4AI_INTEGRATION.md` must contain exact commands:

```bash
openssl rand -hex 32
# put value in .env as CRAWL4AI_API_TOKEN

docker compose --profile searxng --profile crawl4ai pull
docker compose --profile searxng --profile crawl4ai up -d --build

docker compose ps
docker compose exec bot python scripts/smoke_crawl4ai.py
```

Document rollback:

```dotenv
CRAWL4AI_ENABLED=0
```

followed by bot restart; Crawl4AI container may then be stopped independently.

- [ ] **Step 3: Document architecture boundaries**

Update `README.md` / `docs/SEARCH_RESILIENCE.md` to state explicitly:

```text
Search discovery: SearXNG -> DDGS fallback
URL reading: X-specific -> Crawl4AI -> generic reader
```

Do not claim Crawl4AI improves search ranking or replaces SearXNG.

- [ ] **Step 4: Document version-promotion policy**

For upgrades from 0.9.3:

1. read upstream security/release notes;
2. change pinned image on a branch;
3. run offline tests;
4. run live smoke against representative static/JS pages;
5. inspect RAM/CPU and fallback rate;
6. promote only after successful observation;
7. never switch production directly to `latest`.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_crawl4ai.py docs/CRAWL4AI_INTEGRATION.md README.md docs/SEARCH_RESILIENCE.md
git commit -m "docs: add Crawl4AI rollout and smoke checks"
```

---

### Task 7: Full verification, failure injection, and audit loop

**Files:**
- Modify tests only when a discovered regression needs a permanent test.

**Interfaces:**
- No new production interface; this is the release gate.

- [ ] **Step 1: Run formatting/lint/compile checks**

Run repository-standard commands, including at minimum:

```bash
ruff check .
python -m compileall -q app tests scripts
```

Expected: exit 0.

- [ ] **Step 2: Run targeted suites**

```bash
python -m unittest tests.test_crawl4ai_client -v
python -m unittest tests.test_url_service -v
python -m unittest tests.test_url_tool_integration -v
python -m unittest tests.test_crawl4ai_compose -v
```

Expected: all PASS.

- [ ] **Step 3: Run full offline regression suite**

Use the repository’s standard full runner:

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p "test_*.py"
```

Expected: all PASS.

- [ ] **Step 4: Validate Compose and images**

```bash
docker compose config --quiet
docker compose --profile searxng --profile crawl4ai pull
docker compose --profile searxng --profile crawl4ai build bot
```

Expected: exit 0; verify resolved Crawl4AI image is version-pinned and has no host port binding.

- [ ] **Step 5: Run private live smoke**

After configuring a random token:

```bash
docker compose --profile searxng --profile crawl4ai up -d
docker compose ps
docker compose exec bot python scripts/smoke_crawl4ai.py
```

Expected: `/health` succeeds and at least the static-page crawl returns non-empty Markdown.

- [ ] **Step 6: Perform failure injection**

Verify each case while the bot remains functional:

```text
Crawl4AI container stopped -> generic reader succeeds
wrong token -> 401/403 -> generic reader succeeds
Crawl4AI timeout -> generic reader succeeds
CRAWL4AI_ENABLED=0 -> Crawl4AI never called
private/localhost user URL -> blocked before both readers
X status URL -> specialized X reader remains first
bot cancellation -> no generic fallback is launched
```

Record representative logs and confirm no token appears.

- [ ] **Step 7: Measure resource/latency before promotion**

For at least 20 representative URLs, capture:

```text
Crawl4AI success rate
fallback-to-generic rate
p50/p95 fetch_url latency
container RAM/CPU peak
static vs JS-heavy extraction quality
```

Initial acceptance target: no regression in bot availability, no unbounded memory growth, and Crawl4AI must materially improve at least the JS-heavy/poor-generic-reader cases. Do not invent fixed latency/RAM thresholds before observing the target VPS.

- [ ] **Step 8: Security/reliability audit**

Review specifically for:

```text
host port accidentally published
blank token causing inaccessible/misleading deployment
Authorization token in logs/errors
trust_env accidentally enabled for private service client
user-controlled Crawl4AI config/hooks/JS/provider fields
redirected_url accepted without public-URL validation
CancelledError swallowed
multiple Crawl4AI retries per fetch
multiple generic fallbacks per fetch
search router changed unnecessarily
shutdown leaking httpx client
```

For every Critical/High finding: add or identify a regression test first, fix root cause, rerun targeted + full suites, and audit again until Critical=0 and High=0.

- [ ] **Step 9: Commit final regression fixes**

```bash
git add app tests scripts docs docker-compose.yml .env.example README.md
git commit -m "fix: harden Crawl4AI integration after verification"
```

Skip this commit if verification finds no changes.

---

### Task 8: Final branch review and pull request

**Files:** all changed files from Tasks 1–7.

- [ ] **Step 1: Review branch diff against `main`**

Reject the branch if it contains any of these scope violations:

```text
SearXNG/DDGS router rewritten without a regression requirement
Ketch/Firecrawl/GPT Researcher added
new LLM credentials/configuration for Crawl4AI
Crawl4AI port published to host
execute_js/hooks enabled
existing generic reader removed
X specialized path weakened
latest-tag production dependency
```

- [ ] **Step 2: Verify final CI/test evidence at branch head**

Re-run the full verification commands from Task 7 after the last commit.

- [ ] **Step 3: Open PR**

Create a PR from the implementation branch to `main` with:

```text
Summary
- Crawl4AI private URL-reading backend
- existing SearXNG/DDGS search unchanged
- X -> Crawl4AI -> generic routing
- feature-flag rollback

Security
- pinned 0.9.3 image
- no published port
- bearer token
- bot-side + server-side SSRF validation
- no LLM keys, hooks, or arbitrary JS

Verification
- targeted unit tests
- full regression suite
- compose validation/build
- live private smoke
- failure-injection results
- VPS RAM/CPU/latency observations

Rollback
- CRAWL4AI_ENABLED=0 and restart bot
```

Do not merge automatically; leave the PR for user review.
