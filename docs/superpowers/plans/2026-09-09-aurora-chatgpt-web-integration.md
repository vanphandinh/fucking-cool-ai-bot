# Aurora ChatGPT Web Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Aurora as an optional private ChatGPT Web text gateway immediately after B.AI in the existing provider router, with isolated credentials, temporary auth-failure cooldown, tool-loop compatibility, and a version-pinned Docker sidecar.

**Architecture:** Reuse the existing `OpenAICompatProvider` and `AIProviderRouter`; Aurora is a thin provider factory, not a second client stack. Run `ghcr.io/aurora-develop/aurora:v2.6.3` as an opt-in private Compose sidecar, keep ChatGPT credentials inside that service, and expose only an internal Aurora service key to the Python bot. Preserve B.AI-first routing, Fresh Qwen Synthesis, request-wide tool budgets, and all existing fallback behavior.

**Tech Stack:** Python 3.11/3.12, `httpx`, `unittest`, Pydantic Settings, Docker Compose, Aurora `v2.6.3`, GitHub Actions, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-09-aurora-chatgpt-web-integration-design.md`

## Execution bootstrap for a fresh chat

- Repository: `vanphandinh/fucking-cool-ai-bot`
- Planning branch: `docs/aurora-integration-plan`
- Approved design commit: `8c2ff30114adb757b464c90708bd81ce3d3ea4f1`
- Design baseline: `main` commit `e9c1cfaf3fdecfdb714c701d544ea027fa31c1fe` (merged PR #37, Fresh Qwen Synthesis follow-up).
- Read the spec and this plan in full before editing runtime code.
- At execution time, inspect the latest `main`. If another accepted provider-cleanup change has removed Gemini/Groq/Cloudflare/OpenRouter, rebase the planning docs onto that latest `main` and **do not resurrect removed providers**. Keep the same Aurora boundaries and make the effective order `bai,aurora` if those are the only remaining text providers.
- If the environment supports worktrees, use `superpowers:using-git-worktrees`; otherwise create `feat/aurora-chatgpt-web-integration` from the planning branch after syncing it with latest `main`.
- Use TDD for every runtime behavior change.
- Do not add real ChatGPT or Aurora credentials to Git, tests, CI, PR text, logs, or fixtures.
- Do not merge the implementation PR without explicit user approval.

## Global Constraints

- Aurora is an optional text provider only in v1.
- B.AI remains the primary text provider.
- With the current provider baseline, default order is exactly `bai,aurora,gemini,groq,cloudflare,openrouter`.
- If an approved cleanup removes later providers before implementation, do not re-add them; preserve `bai,aurora` ordering among the providers that remain.
- Aurora runs as an external/private service; do not vendor, fork, or reimplement Aurora inside the Python app.
- Initial Aurora runtime image is pinned to `ghcr.io/aurora-develop/aurora:v2.6.3`; do not use `latest` as the repository default.
- Aurora has no host-published port by default.
- Bot requests contain only the Aurora service key; ChatGPT `session_token`, `refresh_token`, and `access_token` never enter Python settings.
- Aurora defaults: `FREE_ACCOUNTS=false`, `ENABLE_EXTERNAL_TOKEN=false`, `ENABLE_HISTORY=false`, `TOOL_CALLING_ENABLED=true`, `STREAM_MODE=false`, `REFUSAL_RETRIES=3`.
- The bot remains the sole conversation-history owner.
- Do not add Responses API, vision routing, file upload, image generation/editing, audio, TTS, or STT in this change.
- Do not change B.AI model selection or Fresh Qwen Synthesis semantics.
- Do not change `MAX_TOOL_ROUNDS`, `_MAX_TOOL_CALLS_TOTAL`, or `_MAX_PARALLEL_TOOL_CALLS`.
- Do not change SearXNG, DDGS, Crawl4AI, URL caching, source selection, or search behavior.
- Existing providers retain permanent in-process disable behavior on 401/403.
- Aurora 401/403 uses temporary cooldown; default cooldown is exactly 60 seconds unless `Retry-After` is present.
- 429 and 5xx retain current generic provider-health behavior.
- Aurora tool execution remains client-side in the bot; Aurora only converts tool-call protocol.
- Never execute leaked/malformed raw `<tool_call>` markup directly.
- CI remains credential-free and must not require a live Aurora or ChatGPT session.

---

## File Structure

**Create**

- `app/ai/aurora.py` — thin Aurora `OpenAICompatProvider` factory and Aurora-specific health-policy configuration.
- `tests/test_aurora_health.py` — generic auth-failure policy regressions plus Aurora temporary-cooldown behavior.
- `tests/test_aurora_provider.py` — Aurora factory, wire contract, response parsing, and tool-loop compatibility.
- `tests/test_aurora_routing.py` — provider order and B.AI -> Aurora fallback regressions.
- `tests/test_aurora_compose.py` — static private-sidecar/security contract.
- `scripts/probe_aurora.py` — manual credentialed `/v1/models`, chat, and forced tool-call smoke probe.
- `tests/test_aurora_probe.py` — offline tests for probe configuration/redaction/response validation.

**Modify**

- `app/ai/health.py` — add generic optional auth-failure cooldown policy.
- `app/ai/base.py` — allow providers to configure that policy without router name checks.
- `app/config.py` — Aurora settings, configured-provider detection, default order slot.
- `app/ai/router.py` — register/build the Aurora provider in the existing text pool.
- `tests/test_provider_order.py` — update default order and configured-provider regressions.
- `.env.example` — Aurora service/deployment settings only; no ChatGPT tokens.
- `.gitignore` — ignore local Aurora ChatGPT credential files.
- `docker-compose.yml` — add opt-in private `aurora` sidecar.
- `README.md` — deployment, credential boundary, rollout, rollback, and unofficial-upstream warning.
- `.github/workflows/audit.yml` — explicit targeted Aurora tests and Compose-profile validation if not already covered by the full suite.

**Do not modify unless a failing regression proves it necessary**

- `app/ai/synthesis.py`
- `app/ai/bai.py`
- `app/ai/multimodal.py`
- search/Crawl4AI modules
- Telegram handlers/core request model

---

### Task 1: Add a generic temporary auth-failure health policy

**Files:**
- Create: `tests/test_aurora_health.py`
- Modify: `app/ai/health.py`
- Modify: `app/ai/base.py`

**Interfaces:**
- Produces: `ProviderHealth(auth_failure_cooldown_sec: float | None = None)`.
- Produces: `OpenAICompatProvider(..., auth_failure_cooldown_sec: float | None = None)`.
- Default `None` preserves current permanent-disable behavior for 401/403.
- A numeric value causes 401/403 to set cooldown instead of `disabled=True`.

- [ ] **Step 1: Write failing health-policy tests**

Create `tests/test_aurora_health.py`:

```python
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.ai.health import ProviderHealth


class ProviderAuthHealthPolicyTests(unittest.TestCase):
    def test_existing_provider_auth_failure_still_disables_permanently(self) -> None:
        health = ProviderHealth()

        health.record_error("bad key", status_code=401, transient=False)

        self.assertTrue(health.disabled)
        self.assertFalse(health.available())

    def test_temporary_auth_policy_uses_60_second_cooldown(self) -> None:
        health = ProviderHealth(auth_failure_cooldown_sec=60.0)

        with patch("app.ai.health.time.monotonic", return_value=100.0):
            health.record_error("session stale", status_code=401, transient=False)

        self.assertFalse(health.disabled)
        self.assertEqual(health.cooldown_until, 160.0)
        with patch("app.ai.health.time.monotonic", return_value=159.0):
            self.assertFalse(health.available())
        with patch("app.ai.health.time.monotonic", return_value=161.0):
            self.assertTrue(health.available())

    def test_temporary_auth_policy_prefers_retry_after(self) -> None:
        health = ProviderHealth(auth_failure_cooldown_sec=60.0)

        with patch("app.ai.health.time.monotonic", return_value=100.0):
            health.record_error(
                "temporarily blocked",
                status_code=403,
                retry_after=12.0,
                transient=False,
            )

        self.assertFalse(health.disabled)
        self.assertEqual(health.cooldown_until, 112.0)
```

- [ ] **Step 2: Run RED**

```bash
python -m unittest tests.test_aurora_health -v
```

Expected: FAIL because `ProviderHealth` does not accept `auth_failure_cooldown_sec` and current 401/403 always disables.

- [ ] **Step 3: Implement the minimal generic health policy**

Modify `app/ai/health.py` so the dataclass begins with:

```python
@dataclass
class ProviderHealth:
    auth_failure_cooldown_sec: float | None = None
    cooldown_until: float = 0.0
    disabled: bool = False
    consecutive_transient_failures: int = 0
    last_error: str | None = None
```

Replace the current 401/403 branch in `record_error()` with:

```python
if status_code in (401, 403):
    if self.auth_failure_cooldown_sec is None:
        self.disabled = True
    else:
        delay = retry_after if retry_after is not None else self.auth_failure_cooldown_sec
        self.cooldown_until = now + max(0.0, delay)
    return
```

Modify the end of `OpenAICompatProvider.__init__()` parameters in `app/ai/base.py`:

```python
extra_headers: dict[str, str] | None = None,
capabilities: ProviderCapabilities | None = None,
auth_failure_cooldown_sec: float | None = None,
```

and replace:

```python
self.health = ProviderHealth()
```

with:

```python
self.health = ProviderHealth(
    auth_failure_cooldown_sec=auth_failure_cooldown_sec,
)
```

Do not branch on provider name anywhere.

- [ ] **Step 4: Run GREEN plus existing provider regressions**

```bash
python -m unittest tests.test_aurora_health -v
python -m unittest tests.test_bai_provider -v
python -m unittest tests.test_provider_metadata -v
```

Expected: PASS. Existing providers still use permanent 401/403 disable by default.

- [ ] **Step 5: Commit**

```bash
git add app/ai/health.py app/ai/base.py tests/test_aurora_health.py
git commit -m "feat: support temporary provider auth cooldown"
```

---

### Task 2: Add Aurora settings and provider factory

**Files:**
- Create: `app/ai/aurora.py`
- Create/extend: `tests/test_aurora_provider.py`
- Modify: `app/config.py`

**Interfaces:**
- Consumes: `OpenAICompatProvider(..., auth_failure_cooldown_sec=...)` from Task 1.
- Produces: `make_aurora_provider(settings: Settings, *, name: str = "aurora") -> OpenAICompatProvider`.
- Produces settings: `aurora_base_url`, `aurora_api_key`, `aurora_model`, `aurora_request_timeout_sec`.
- Exact auth cooldown: `60.0` seconds.

- [ ] **Step 1: Write failing factory/config tests**

Create `tests/test_aurora_provider.py` with the first test group:

```python
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

import app.ai.aurora as aurora
from app.config import Settings


class AuroraProviderFactoryTests(unittest.TestCase):
    def test_default_settings_are_private_sidecar_friendly(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.aurora_base_url, "http://aurora:8080/v1")
        self.assertEqual(settings.aurora_model, "auto")
        self.assertEqual(settings.aurora_request_timeout_sec, 90.0)
        self.assertEqual(settings.aurora_api_key, "")

    def test_factory_builds_text_only_provider_with_temporary_auth_policy(self) -> None:
        settings = SimpleNamespace(
            aurora_base_url="http://aurora:8080/v1",
            aurora_api_key="internal-secret",
            aurora_model="auto",
            aurora_request_timeout_sec=90.0,
        )

        provider = aurora.make_aurora_provider(settings)
        try:
            self.assertEqual(provider.name, "aurora")
            self.assertEqual(provider.model, "auto")
            self.assertEqual(str(provider._client.base_url), "http://aurora:8080/v1/")
            self.assertEqual(provider.capabilities.route, "text")
            self.assertFalse(provider.capabilities.supports_vision)
            self.assertEqual(provider.capabilities.max_images, 0)
            self.assertEqual(provider.health.auth_failure_cooldown_sec, 60.0)
        finally:
            asyncio.run(provider.aclose())
```

- [ ] **Step 2: Run RED**

```bash
python -m unittest tests.test_aurora_provider.AuroraProviderFactoryTests -v
```

Expected: FAIL because `app.ai.aurora` and Aurora settings do not exist.

- [ ] **Step 3: Add Aurora settings**

In `Settings` in `app/config.py`, place Aurora beside the text-provider settings:

```python
aurora_base_url: str = "http://aurora:8080/v1"
aurora_api_key: str = ""
aurora_model: str = "auto"
aurora_request_timeout_sec: float = Field(default=90.0, gt=0, allow_inf_nan=False)
```

Do not add ChatGPT session/refresh/access token fields.

- [ ] **Step 4: Add the thin provider factory**

Create `app/ai/aurora.py`:

```python
"""Aurora ChatGPT Web gateway provider integration."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities

_AUTH_FAILURE_COOLDOWN_SEC = 60.0


def make_aurora_provider(
    settings: Settings,
    *,
    name: str = "aurora",
) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name=name,
        base_url=settings.aurora_base_url,
        api_key=settings.aurora_api_key,
        model=settings.aurora_model,
        timeout=settings.aurora_request_timeout_sec,
        capabilities=ProviderCapabilities(
            route="text",
            supports_vision=False,
            max_images=0,
        ),
        auth_failure_cooldown_sec=_AUTH_FAILURE_COOLDOWN_SEC,
    )
```

Do not add custom request serialization; use the existing Chat Completions contract.

- [ ] **Step 5: Run GREEN**

```bash
python -m unittest tests.test_aurora_provider.AuroraProviderFactoryTests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/ai/aurora.py tests/test_aurora_provider.py
git commit -m "feat: add Aurora provider factory"
```

---

### Task 3: Register Aurora in provider order and fallback routing

**Files:**
- Create: `tests/test_aurora_routing.py`
- Modify: `app/config.py`
- Modify: `app/ai/router.py`
- Modify: `tests/test_provider_order.py`

**Interfaces:**
- Consumes: `make_aurora_provider()` from Task 2.
- Produces current-baseline default order: `bai,aurora,gemini,groq,cloudflare,openrouter`.
- Aurora is configured only when `aurora_base_url`, `aurora_api_key`, and `aurora_model` are non-empty.

- [ ] **Step 1: Write failing order/configuration tests**

Update the default assertion in `tests/test_provider_order.py`:

```python
self.assertEqual(
    settings.text_provider_order_list,
    ["bai", "aurora", "gemini", "groq", "cloudflare", "openrouter"],
)
```

Create `tests/test_aurora_routing.py`:

```python
from __future__ import annotations

import asyncio
import unittest

from app.ai.router import build_provider_router
from app.config import Settings


class AuroraRoutingConfigurationTests(unittest.TestCase):
    def test_aurora_is_absent_without_service_key(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="b",
            aurora_api_key="",
            gemini_api_key="g",
            vision_enabled=False,
        )

        self.assertEqual(settings.configured_provider_names, ["bai", "gemini"])

    def test_aurora_is_immediately_after_bai_when_configured(self) -> None:
        settings = Settings(
            _env_file=None,
            bai_api_key="b",
            aurora_api_key="a",
            gemini_api_key="g",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        try:
            self.assertEqual(
                settings.configured_provider_names,
                ["bai", "aurora", "gemini"],
            )
            self.assertEqual(
                [p.name for p in router.capable_providers(requires_vision=False)],
                ["bai", "aurora", "gemini"],
            )
        finally:
            asyncio.run(_close_router(router))

    def test_aurora_config_is_ignored_when_removed_from_order(self) -> None:
        settings = Settings(
            _env_file=None,
            aurora_api_key="a",
            groq_api_key="g",
            text_provider_order="groq",
            vision_enabled=False,
        )
        router = build_provider_router(settings)
        try:
            self.assertEqual([p.name for p in router.providers], ["groq"])
        finally:
            asyncio.run(_close_router(router))


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()
```

- [ ] **Step 2: Run RED**

```bash
python -m unittest tests.test_provider_order tests.test_aurora_routing -v
```

Expected: FAIL because the default order and router do not contain Aurora.

- [ ] **Step 3: Add the Aurora slot to settings**

Change the current-baseline default in `app/config.py`:

```python
text_provider_order: str = "bai,aurora,gemini,groq,cloudflare,openrouter"
```

In `configured_provider_names`, add:

```python
if self.aurora_base_url.strip() and self.aurora_api_key and self.aurora_model:
    available.add("aurora")
```

If the implementation baseline has already removed later providers, use the equivalent surviving default `bai,aurora` and update only tests that correspond to that accepted cleanup. Do not reintroduce deleted provider fields/factories.

- [ ] **Step 4: Wire the factory into the router**

At imports in `app/ai/router.py`:

```python
from .aurora import make_aurora_provider
```

In the text-provider construction block, add:

```python
if (
    "aurora" in settings.text_provider_order_list
    and settings.aurora_base_url.strip()
    and settings.aurora_api_key
    and settings.aurora_model
):
    text["aurora"] = make_aurora_provider(settings)
```

Keep the existing loop that appends providers according to `text_provider_order_list`; do not create a separate Aurora fallback path.

- [ ] **Step 5: Run GREEN plus routing regressions**

```bash
python -m unittest tests.test_provider_order tests.test_aurora_routing -v
python -m unittest tests.test_free_routing -v
python -m unittest tests.test_fresh_synthesis_routing -v
```

Expected: PASS. Fresh Qwen behavior is unchanged.

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/ai/router.py tests/test_provider_order.py tests/test_aurora_routing.py
git commit -m "feat: route Aurora after B.AI"
```

---

### Task 4: Verify Aurora Chat Completions and the existing tool loop

**Files:**
- Modify: `tests/test_aurora_provider.py`
- Modify only if a failing compatibility test proves necessary: `app/ai/base.py`, `app/ai/router.py`

**Interfaces:**
- Consumes: Aurora factory and existing `OpenAICompatProvider.chat()`.
- Consumes: existing `AIProviderRouter.complete()` tool executor loop.
- Produces no new public interface unless a real compatibility defect is demonstrated by RED.

- [ ] **Step 1: Add wire-contract and tool-call parsing tests**

Append to `tests/test_aurora_provider.py`:

```python
import json
import httpx

from app.ai.router import AIProviderRouter


class AuroraWireContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_uses_service_bearer_key_and_openai_payload(self) -> None:
        seen: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
                request=request,
            )

        provider = _mocked_aurora_provider(respond)
        try:
            result = await provider.chat(
                [{"role": "user", "content": "hello"}],
                [_search_tool()],
            )
        finally:
            await provider.aclose()

        self.assertEqual(result.content, "ok")
        self.assertEqual(seen[0].url.path, "/v1/chat/completions")
        self.assertEqual(seen[0].headers["authorization"], "Bearer internal-secret")
        payload = json.loads(seen[0].content)
        self.assertEqual(payload["model"], "auto")
        self.assertEqual(set(payload), {"model", "messages", "tools"})

    async def test_openai_tool_call_is_parsed(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "call_aurora",
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"query":"aurora"}',
                                },
                            }],
                        }
                    }]
                },
                request=request,
            )

        provider = _mocked_aurora_provider(respond)
        try:
            result = await provider.chat(
                [{"role": "user", "content": "search"}],
                [_search_tool()],
            )
        finally:
            await provider.aclose()

        self.assertEqual(result.tool_calls[0].id, "call_aurora")
        self.assertEqual(result.tool_calls[0].name, "web_search")
        self.assertEqual(result.tool_calls[0].arguments, {"query": "aurora"})
```

Add helpers using the exact factory rather than constructing another client path:

```python
def _mocked_aurora_provider(handler) -> object:
    settings = SimpleNamespace(
        aurora_base_url="http://aurora:8080/v1",
        aurora_api_key="internal-secret",
        aurora_model="auto",
        aurora_request_timeout_sec=90.0,
    )
    provider = aurora.make_aurora_provider(settings)
    asyncio.run(provider.aclose()) if False else None
    provider._client = httpx.AsyncClient(
        base_url="http://aurora:8080/v1/",
        headers={"Authorization": "Bearer internal-secret"},
        transport=httpx.MockTransport(handler),
    )
    return provider


def _search_tool() -> dict:
    return {
        "type": "function",
        "function": {
            "name": "web_search",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
```

When implementing the test helper, close the factory-created client with `await provider.aclose()` inside the async test before replacing `_client`, exactly as `tests/test_bai_provider.py` does; do not keep the illustrative unreachable `asyncio.run(... if False ...)` line above. The final helper should accept an already-created provider or be async so there is no leaked client.

- [ ] **Step 2: Run the wire tests**

```bash
python -m unittest tests.test_aurora_provider.AuroraWireContractTests -v
```

Expected: PASS using the existing generic provider. If it fails, fix only the demonstrated compatibility gap; do not fork Chat Completions serialization for Aurora speculatively.

- [ ] **Step 3: Add a full client-side tool round-trip regression**

Add a test where the mock transport returns a tool call on request 1 and final text on request 2. The key assertions are:

```python
self.assertEqual(answer, "final from aurora")
self.assertEqual(provider_name, "aurora")
self.assertEqual(executed, [("web_search", {"query": "aurora"})])
self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
self.assertEqual(requests[1]["messages"][-1]["tool_call_id"], "call_aurora")
```

Use:

```python
router = AIProviderRouter([provider], max_tool_rounds=2)
answer, provider_name = await router.complete(
    [{"role": "user", "content": "find aurora"}],
    [_search_tool()],
    tool_executor,
)
```

The mock handler should branch on call count:

```python
if len(requests) == 1:
    return httpx.Response(200, json={"choices": [{"message": { ... tool_calls ... }}]}, request=request)
return httpx.Response(
    200,
    json={"choices": [{"message": {"role": "assistant", "content": "final from aurora"}}]},
    request=request,
)
```

- [ ] **Step 4: Add malformed internal-markup safety regression**

Return assistant content containing malformed internal tool markup, for example:

```text
<tool_call>unknown_tool<arg_key>x</arg_key><arg_value>1</arg_value></tool_call>
```

Call `provider.chat(..., [_search_tool()])` and assert `ProviderError` is raised. The test must prove the unknown tool is not surfaced as a valid `ToolCall`.

- [ ] **Step 5: Run GREEN plus tool-budget/fresh-synthesis suites**

```bash
python -m unittest tests.test_aurora_provider -v
python -m unittest tests.test_tool_fallback_recovery -v
python -m unittest tests.test_fresh_synthesis_routing -v
```

Expected: PASS without changing tool budgets or synthesis code.

- [ ] **Step 6: Commit**

```bash
git add tests/test_aurora_provider.py app/ai/base.py app/ai/router.py
git commit -m "test: verify Aurora chat and tool compatibility"
```

If `app/ai/base.py` and `app/ai/router.py` were unchanged in this task, omit them from `git add`.

---

### Task 5: Add the private version-pinned Aurora sidecar and credential boundary

**Files:**
- Create: `tests/test_aurora_compose.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Produces Compose profile: `aurora`.
- Produces container DNS endpoint: `http://aurora:8080/v1`.
- Produces deploy variables: `AURORA_IMAGE`, `AURORA_CREDENTIAL_FILE`, `AURORA_CREDENTIAL_TARGET`.
- Default credential source: `./aurora/session_tokens.txt` -> `/session_tokens.txt` read-only.
- Refresh-token alternative: set target `/refresh_tokens.txt` and source to the local refresh-token file.

- [ ] **Step 1: Write failing Compose/security contract tests**

Create `tests/test_aurora_compose.py`:

```python
from __future__ import annotations

from pathlib import Path
import unittest


class AuroraComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = Path("docker-compose.yml").read_text(encoding="utf-8")
        marker = "\n  aurora:\n"
        cls.assertIn(cls, marker, cls.compose)
        cls.block = cls.compose.split(marker, 1)[1].split("\nvolumes:", 1)[0]
        cls.env = Path(".env.example").read_text(encoding="utf-8")
        cls.gitignore = Path(".gitignore").read_text(encoding="utf-8")

    def test_aurora_is_profiled_private_and_version_pinned(self) -> None:
        self.assertIn('profiles: ["aurora"]', self.block)
        self.assertIn("ghcr.io/aurora-develop/aurora:v2.6.3", self.block)
        self.assertNotIn("ports:", self.block)
        self.assertNotIn("8080:8080", self.block)

    def test_aurora_disables_external_tokens_history_and_free_pool(self) -> None:
        self.assertIn('ENABLE_EXTERNAL_TOKEN: "false"', self.block)
        self.assertIn('ENABLE_HISTORY: "false"', self.block)
        self.assertIn('FREE_ACCOUNTS: "false"', self.block)
        self.assertIn('TOOL_CALLING_ENABLED: "true"', self.block)
        self.assertIn('STREAM_MODE: "false"', self.block)
        self.assertIn('REFUSAL_RETRIES: "3"', self.block)

    def test_chatgpt_credentials_are_file_mounted_read_only_not_env_fields(self) -> None:
        self.assertIn("AURORA_CREDENTIAL_FILE", self.block)
        self.assertIn("AURORA_CREDENTIAL_TARGET", self.block)
        self.assertIn("read_only: true", self.block)
        self.assertNotIn("SESSION_TOKEN=", self.env)
        self.assertNotIn("REFRESH_TOKEN=", self.env)
        self.assertNotIn("ACCESS_TOKEN=", self.env)
        self.assertIn("aurora/session_tokens.txt", self.gitignore)
        self.assertIn("aurora/refresh_tokens.txt", self.gitignore)
        self.assertIn("aurora/access_tokens.txt", self.gitignore)
```

- [ ] **Step 2: Run RED**

```bash
python -m unittest tests.test_aurora_compose -v
```

Expected: FAIL because the Compose sidecar and deployment settings do not exist.

- [ ] **Step 3: Add Aurora service settings to `.env.example`**

Add a dedicated section:

```dotenv
# ============ Aurora / ChatGPT Web fallback ============
# Internal Aurora service key only. Never put ChatGPT session/access/refresh tokens here.
AURORA_BASE_URL=http://aurora:8080/v1
AURORA_API_KEY=
AURORA_MODEL=auto
AURORA_REQUEST_TIMEOUT_SEC=90.0
AURORA_IMAGE=ghcr.io/aurora-develop/aurora:v2.6.3
# Default: ChatGPT Web session-token file. For refresh tokens, point source to that
# local file and change target to /refresh_tokens.txt.
AURORA_CREDENTIAL_FILE=./aurora/session_tokens.txt
AURORA_CREDENTIAL_TARGET=/session_tokens.txt
```

Keep the actual token out of the environment file.

- [ ] **Step 4: Ignore local ChatGPT credential files**

Append to `.gitignore`:

```gitignore
# Aurora ChatGPT Web credentials — never commit.
aurora/session_tokens.txt
aurora/refresh_tokens.txt
aurora/access_tokens.txt
```

- [ ] **Step 5: Add the private Compose service**

Add under `services:` in `docker-compose.yml`:

```yaml
  aurora:
    image: ${AURORA_IMAGE:-ghcr.io/aurora-develop/aurora:v2.6.3}
    container_name: fcai-aurora
    profiles: ["aurora"]
    restart: unless-stopped
    init: true
    stop_grace_period: 30s
    environment:
      SERVER_HOST: "0.0.0.0"
      SERVER_PORT: "8080"
      Authorization: ${AURORA_API_KEY:-}
      FREE_ACCOUNTS: "false"
      ENABLE_EXTERNAL_TOKEN: "false"
      ENABLE_HISTORY: "false"
      TOOL_CALLING_ENABLED: "true"
      STREAM_MODE: "false"
      REFUSAL_RETRIES: "3"
    volumes:
      - type: bind
        source: ${AURORA_CREDENTIAL_FILE:-./aurora/session_tokens.txt}
        target: ${AURORA_CREDENTIAL_TARGET:-/session_tokens.txt}
        read_only: true
```

Do not add `ports:`. Do not add a hard `bot -> aurora depends_on` relationship.

- [ ] **Step 6: Document operator setup, canary, and rollback**

Update `README.md` with an Aurora subsection containing these operational facts:

```text
1. Aurora is an unofficial ChatGPT Web gateway and can break when chatgpt.com changes.
2. mkdir -p aurora && chmod 700 aurora
3. create aurora/session_tokens.txt locally; one token per line; chmod 600 the file
4. generate a strong AURORA_API_KEY distinct from the ChatGPT credential
5. start with: docker compose --profile aurora up -d aurora
6. first validate with scripts/probe_aurora.py
7. canary with TEXT_PROVIDER_ORDER=bai,aurora,...
8. rollback by removing aurora from TEXT_PROVIDER_ORDER and stopping the profile
```

Also document the refresh-token alternative using:

```dotenv
AURORA_CREDENTIAL_FILE=./aurora/refresh_tokens.txt
AURORA_CREDENTIAL_TARGET=/refresh_tokens.txt
```

Do not document copying ChatGPT tokens into `AURORA_API_KEY`.

- [ ] **Step 7: Run GREEN and Compose validation**

```bash
python -m unittest tests.test_aurora_compose -v
cp .env.example .env
mkdir -p aurora
touch aurora/session_tokens.txt
docker compose --profile aurora config --quiet
rm -f .env aurora/session_tokens.txt
rmdir aurora 2>/dev/null || true
```

Expected: PASS. `docker compose config` must not require a real token or pull/run the image.

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml .env.example .gitignore README.md tests/test_aurora_compose.py
git commit -m "feat: add private Aurora sidecar"
```

---

### Task 6: Add an operator-safe Aurora smoke probe

**Files:**
- Create: `scripts/probe_aurora.py`
- Create: `tests/test_aurora_probe.py`

**Interfaces:**
- Reads only: `AURORA_BASE_URL`, `AURORA_API_KEY`, `AURORA_MODEL`.
- Modes: `models`, `chat`, `tool`, `all`.
- Never reads Aurora ChatGPT token files.
- Tool mode forces an `echo_probe` function call but never executes it.

- [ ] **Step 1: Write failing probe-helper tests**

Create `tests/test_aurora_probe.py`:

```python
from __future__ import annotations

import unittest

from scripts.probe_aurora import (
    _bounded_redacted,
    _extract_chat_text,
    _extract_tool_call,
)


class AuroraProbeHelperTests(unittest.TestCase):
    def test_output_is_bounded_and_service_key_is_redacted(self) -> None:
        secret = "super-secret-key"
        value = secret + ("x" * 1000)

        result = _bounded_redacted(value, secret=secret, limit=120)

        self.assertNotIn(secret, result)
        self.assertLessEqual(len(result), 120)

    def test_chat_response_extracts_text(self) -> None:
        self.assertEqual(
            _extract_chat_text({"choices": [{"message": {"content": "ok"}}]}),
            "ok",
        )

    def test_tool_response_requires_openai_function_call_shape(self) -> None:
        call = _extract_tool_call({
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "echo_probe",
                            "arguments": '{"value":"aurora"}',
                        },
                    }]
                }
            }]
        })

        self.assertEqual(call["function"]["name"], "echo_probe")
```

- [ ] **Step 2: Run RED**

```bash
python -m unittest tests.test_aurora_probe -v
```

Expected: FAIL because `scripts.probe_aurora` does not exist.

- [ ] **Step 3: Implement response helpers and configuration guard**

Create `scripts/probe_aurora.py` with:

```python
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def _bounded_redacted(value: object, *, secret: str, limit: int = 500) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if secret:
        text = text.replace(secret, "[redacted]")
    return text[:limit]


def _extract_chat_text(data: dict) -> str:
    value = data["choices"][0]["message"]["content"]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Aurora chat response has no text content")
    return value.strip()


def _extract_tool_call(data: dict) -> dict:
    call = data["choices"][0]["message"]["tool_calls"][0]
    function = call.get("function") if isinstance(call, dict) else None
    if not isinstance(function, dict) or function.get("name") != "echo_probe":
        raise ValueError("Aurora did not return the expected echo_probe tool call")
    json.loads(function.get("arguments") or "{}")
    return call


def _config() -> tuple[str, str, str]:
    base_url = os.getenv("AURORA_BASE_URL", "http://aurora:8080/v1").rstrip("/")
    api_key = os.getenv("AURORA_API_KEY", "").strip()
    model = os.getenv("AURORA_MODEL", "auto").strip() or "auto"
    if not api_key:
        raise ValueError("AURORA_API_KEY is required")
    return base_url, api_key, model
```

- [ ] **Step 4: Implement the three probe requests**

Use one synchronous `httpx.Client`:

```python
with httpx.Client(
    base_url=base_url + "/",
    headers={"Authorization": f"Bearer {api_key}"},
    timeout=30.0,
) as client:
    ...
```

`models` mode:

```python
response = client.get("models")
response.raise_for_status()
data = response.json()
if not isinstance(data.get("data"), list) or not data["data"]:
    raise ValueError("Aurora /v1/models returned no models")
print(f"models: ok ({len(data['data'])})")
```

`chat` mode:

```python
response = client.post(
    "chat/completions",
    json={
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: aurora-ok"}],
    },
)
response.raise_for_status()
print("chat:", _bounded_redacted(_extract_chat_text(response.json()), secret=api_key))
```

`tool` mode:

```python
response = client.post(
    "chat/completions",
    json={
        "model": model,
        "messages": [{"role": "user", "content": "Call echo_probe with value aurora."}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "echo_probe",
                "description": "Smoke-test function. Do not answer in prose.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            },
        }],
        "tool_choice": {
            "type": "function",
            "function": {"name": "echo_probe"},
        },
    },
)
response.raise_for_status()
call = _extract_tool_call(response.json())
print("tool: ok", call["id"])
```

The script must catch `httpx.HTTPError`, `ValueError`, `KeyError`, `IndexError`, and JSON errors at the top level, print a bounded/redacted error to stderr, and exit non-zero. It must never print request headers.

- [ ] **Step 5: Add argparse modes**

Use:

```python
parser.add_argument(
    "--mode",
    choices=("models", "chat", "tool", "all"),
    default="all",
)
```

`all` executes `models`, then `chat`, then `tool` and stops on the first failure.

- [ ] **Step 6: Run GREEN**

```bash
python -m unittest tests.test_aurora_probe -v
python -m compileall -q scripts/probe_aurora.py
```

Expected: PASS without network access.

- [ ] **Step 7: Commit**

```bash
git add scripts/probe_aurora.py tests/test_aurora_probe.py
git commit -m "feat: add Aurora smoke probe"
```

---

### Task 7: Add explicit Aurora CI coverage and run the full verification gate

**Files:**
- Modify: `.github/workflows/audit.yml`
- Modify only if verification exposes a real regression: files directly responsible for that regression.

**Interfaces:**
- CI remains offline with respect to Aurora/ChatGPT.
- Python matrix remains exactly 3.11 and 3.12.
- Existing dependency audit and production bot Docker build remain enabled.

- [ ] **Step 1: Add targeted Aurora tests to CI**

In `.github/workflows/audit.yml`, add before the general offline regression step:

```yaml
      - name: Targeted Aurora tests
        run: |
          python -m unittest discover -s tests -p 'test_aurora*.py' -v
```

Update the existing Compose validation command on Python 3.12 to:

```yaml
          cp .env.example .env
          mkdir -p aurora
          touch aurora/session_tokens.txt
          docker compose --profile aurora config --quiet
```

No live container start and no real credential are permitted in CI.

- [ ] **Step 2: Run targeted Aurora suites locally**

```bash
python -m unittest discover -s tests -p 'test_aurora*.py' -v
```

Expected: PASS.

- [ ] **Step 3: Run routing/tool regressions most likely to be affected**

```bash
python -m unittest tests.test_provider_order -v
python -m unittest tests.test_bai_provider -v
python -m unittest tests.test_free_routing -v
python -m unittest tests.test_tool_fallback_recovery -v
python -m unittest tests.test_fresh_synthesis_routing -v
python -m unittest tests.test_router_concurrency -v
```

Expected: PASS.

- [ ] **Step 4: Run the full offline test suite**

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: PASS with no live Aurora/ChatGPT dependency.

- [ ] **Step 5: Run static verification**

```bash
python -m ruff check .
python -m compileall -q app tests scripts
```

Expected: PASS.

- [ ] **Step 6: Validate Compose with the Aurora profile**

```bash
cp .env.example .env
mkdir -p aurora
touch aurora/session_tokens.txt
docker compose --profile aurora config --quiet
rm -f .env aurora/session_tokens.txt
rmdir aurora 2>/dev/null || true
```

Expected: PASS and no Aurora host port in rendered config.

- [ ] **Step 7: Build the production bot image**

```bash
docker build --tag fcai-audit:aurora .
```

Expected: PASS.

- [ ] **Step 8: Inspect the complete diff against the implementation base**

Run:

```bash
git diff --check
git status --short
git diff <implementation-base>...HEAD -- \
  app tests scripts docker-compose.yml .env.example .gitignore README.md .github/workflows/audit.yml
```

Review explicitly for:

```text
- accidental ChatGPT/Aurora secrets
- Aurora host ports
- use of ghcr.io/aurora-develop/aurora:latest as a default
- ENABLE_EXTERNAL_TOKEN=true
- ENABLE_HISTORY=true
- FREE_ACCOUNTS=true
- provider-name special cases in router health logic
- changes to B.AI model/tool budgets/Fresh Qwen synthesis
- accidental resurrection of providers removed by a concurrent accepted cleanup
```

Fix any issue and rerun the affected targeted suite plus Steps 4-7.

- [ ] **Step 9: Commit CI/verification changes**

```bash
git add .github/workflows/audit.yml
git commit -m "ci: cover Aurora integration"
```

- [ ] **Step 10: Push the implementation branch and verify GitHub Actions**

Push `feat/aurora-chatgpt-web-integration`, open a PR to `main`, and verify both Python 3.11 and 3.12 jobs pass. Do not merge automatically.

---

### Task 8: Run the credentialed deployment canary after CI passes

**Files:**
- No committed source changes unless the canary reveals a reproducible integration defect.

**Interfaces:**
- Uses local/uncommitted `aurora/session_tokens.txt` or a refresh-token file.
- Uses `AURORA_API_KEY` only as the bot-to-Aurora service key.
- Produces operational evidence for `/v1/models`, simple chat, forced tool-call parsing, and fallback behavior.

- [ ] **Step 1: Prepare secrets locally on the deployment host**

```bash
mkdir -p aurora
chmod 700 aurora
# Place one valid ChatGPT Web session token per line in aurora/session_tokens.txt.
chmod 600 aurora/session_tokens.txt
```

Generate a distinct strong `AURORA_API_KEY` and place it in the deployment `.env`. Never commit it.

- [ ] **Step 2: Start only Aurora first**

```bash
docker compose --profile aurora up -d aurora
```

Confirm the container is running without publishing port 8080 to the host.

- [ ] **Step 3: Run the manual probe from the private Compose network**

Preferred method: execute the probe from the bot container or a one-off container attached to the same Compose network, so `AURORA_BASE_URL=http://aurora:8080/v1` remains private.

Run:

```bash
python scripts/probe_aurora.py --mode models
python scripts/probe_aurora.py --mode chat
python scripts/probe_aurora.py --mode tool
```

Required result: all three exit 0; tool mode returns a parseable `echo_probe` function call and does not execute it.

- [ ] **Step 4: Canary Aurora as B.AI fallback**

Set the production order to:

```dotenv
TEXT_PROVIDER_ORDER=bai,aurora,gemini,groq,cloudflare,openrouter
```

or `bai,aurora` if the accepted provider cleanup has already removed the later providers.

Restart/redeploy the bot and verify ordinary B.AI-success requests still report B.AI as selected.

- [ ] **Step 5: Exercise one controlled B.AI -> Aurora fallback**

Use a staging/test configuration or controlled provider failure so B.AI fails once without modifying production B.AI model settings. Verify:

```text
- router falls to aurora
- Aurora returns text
- fallback count increments
- no ChatGPT token appears in logs
```

- [ ] **Step 6: Exercise one Aurora tool round-trip**

Use a request that invokes an existing safe read/search tool. Verify:

```text
Aurora tool_calls -> bot executes tool -> role=tool replay -> Aurora final text
```

Confirm request-wide tool budgets remain unchanged.

- [ ] **Step 7: Validate temporary auth recovery behavior**

Only in a staging/test deployment, make Aurora return an auth failure long enough to observe the 60-second cooldown, then restore valid Aurora credentials/session state. Verify the provider becomes eligible again without restarting the bot process.

Do not perform repeated auth failures against a real account merely to stress rate limits.

- [ ] **Step 8: Record canary result in the PR**

Add a PR comment containing only non-secret results:

```text
Aurora v2.6.3 canary
- /v1/models: PASS
- chat: PASS
- forced tool schema: PASS
- B.AI -> Aurora fallback: PASS
- Aurora tool round-trip: PASS
- 401/403 temporary recovery: PASS/NOT EXERCISED
- secrets observed in logs: NO
```

If a canary step fails, do not merge. Reproduce it with an offline/mock regression where possible, fix through TDD, and rerun the relevant verification gate.

---

## Final acceptance checklist

Before requesting merge approval, confirm all items:

- [ ] Aurora is optional and text-only.
- [ ] B.AI remains first.
- [ ] Aurora is immediately after B.AI when configured.
- [ ] Aurora uses the existing `OpenAICompatProvider`; no second HTTP/provider stack was created.
- [ ] The Python bot has no ChatGPT session/access/refresh token settings.
- [ ] Aurora is private in Compose and has no `ports:` exposure.
- [ ] Repository default image is exactly `ghcr.io/aurora-develop/aurora:v2.6.3` or an overridable variable with that exact pinned default.
- [ ] Credential mount is read-only and credential files are Git-ignored.
- [ ] `FREE_ACCOUNTS=false`.
- [ ] `ENABLE_EXTERNAL_TOKEN=false`.
- [ ] `ENABLE_HISTORY=false`.
- [ ] `TOOL_CALLING_ENABLED=true`.
- [ ] `STREAM_MODE=false`.
- [ ] Existing providers still permanently disable on 401/403.
- [ ] Aurora 401/403 cools down for 60 seconds by default and later becomes eligible again.
- [ ] 429/5xx semantics remain unchanged.
- [ ] Aurora text response parsing passes.
- [ ] Aurora OpenAI-format tool parsing passes.
- [ ] A full client-side Aurora tool round-trip passes.
- [ ] Malformed/unknown raw tool markup is not executed.
- [ ] Fresh Qwen Synthesis regressions pass unchanged.
- [ ] Tool budgets and parallelism constants are unchanged.
- [ ] No search/Crawl4AI behavior changed.
- [ ] No removed provider was resurrected after rebasing on latest `main`.
- [ ] All targeted Aurora tests pass.
- [ ] Full offline tests pass.
- [ ] Ruff passes.
- [ ] `compileall` passes.
- [ ] `docker compose --profile aurora config --quiet` passes.
- [ ] Production bot Docker image builds.
- [ ] GitHub Actions passes on Python 3.11 and 3.12.
- [ ] Credentialed `/v1/models`, chat, and tool probe passes before merge.
- [ ] PR remains unmerged until explicit user approval.
