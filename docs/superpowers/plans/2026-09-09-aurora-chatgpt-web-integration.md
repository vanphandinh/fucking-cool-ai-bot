# Aurora ChatGPT Web Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Aurora as an optional private ChatGPT Web text gateway immediately after B.AI, with isolated ChatGPT credentials, OpenAI-compatible chat/tool behavior, temporary auth-failure cooldown, and a pinned Docker sidecar.

**Architecture:** Reuse `OpenAICompatProvider` and `AIProviderRouter`; Aurora is only a thin provider factory. Run `ghcr.io/aurora-develop/aurora:v2.6.3` as an opt-in private Compose sidecar, keep ChatGPT credentials inside Aurora, and expose only an internal Aurora service key to the bot. Preserve B.AI-first routing, Fresh Qwen Synthesis, shared tool budgets, and existing fallback semantics.

**Tech Stack:** Python 3.11/3.12, `httpx`, `unittest`, Pydantic Settings, Docker Compose, Aurora `v2.6.3`, Ruff, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-09-aurora-chatgpt-web-integration-design.md`

## Execution bootstrap

- Repository: `vanphandinh/fucking-cool-ai-bot`
- Planning branch: `docs/aurora-integration-plan`
- Approved design commit: `8c2ff30114adb757b464c90708bd81ce3d3ea4f1`
- Design baseline: `main` commit `e9c1cfaf3fdecfdb714c701d544ea027fa31c1fe`.
- Read the spec and this plan completely before editing runtime code.
- At execution time inspect latest `main`. If an accepted provider-cleanup change has removed Gemini/Groq/Cloudflare/OpenRouter, rebase first and **do not resurrect removed providers**. Preserve `bai,aurora` ordering among providers that remain.
- Create implementation branch `feat/aurora-chatgpt-web-integration` from the planning branch after syncing with latest `main`. Use `superpowers:using-git-worktrees` when available.
- Use TDD for every runtime behavior change.
- Never commit real Aurora/ChatGPT credentials.
- Do not merge the implementation PR without explicit user approval.

## Global Constraints

- Aurora v1 is text-only.
- B.AI stays primary.
- Current-baseline default order is exactly `bai,aurora,gemini,groq,cloudflare,openrouter`; after an accepted cleanup, do not re-add removed slots.
- Do not vendor/fork/reimplement Aurora inside the bot.
- Pin repository default Aurora image to `ghcr.io/aurora-develop/aurora:v2.6.3`, never `latest`.
- Do not publish Aurora port 8080 to the host.
- Bot settings contain only the Aurora service key; no ChatGPT `session_token`, `refresh_token`, or `access_token` fields.
- Aurora defaults are exactly: `FREE_ACCOUNTS=false`, `ENABLE_EXTERNAL_TOKEN=false`, `ENABLE_HISTORY=false`, `TOOL_CALLING_ENABLED=true`, `STREAM_MODE=false`, `REFUSAL_RETRIES=3`.
- Bot remains sole conversation-history owner.
- Do not add Responses API, vision, files, image generation/editing, audio, TTS, or STT.
- Do not change B.AI model selection, Fresh Qwen Synthesis, `MAX_TOOL_ROUNDS`, `_MAX_TOOL_CALLS_TOTAL`, or `_MAX_PARALLEL_TOOL_CALLS`.
- Do not change SearXNG/DDGS/Crawl4AI/search behavior.
- Existing providers retain permanent in-process disable on 401/403.
- Aurora 401/403 uses temporary 60-second cooldown unless `Retry-After` is present.
- 429/5xx retain current generic health behavior.
- Tools execute only in the bot; Aurora only converts tool-call protocol.
- Never execute malformed/leaked raw `<tool_call>` markup.
- CI remains credential-free and offline with respect to Aurora/ChatGPT.

## File Structure

**Create**
- `app/ai/aurora.py` — Aurora provider factory.
- `tests/test_aurora_health.py` — auth health-policy regressions.
- `tests/test_aurora_provider.py` — factory, wire, tool-call, and full tool-loop tests.
- `tests/test_aurora_routing.py` — provider order/registration tests.
- `tests/test_aurora_compose.py` — private-sidecar/security contract.
- `scripts/probe_aurora.py` — credentialed manual smoke probe.
- `tests/test_aurora_probe.py` — offline probe helper/image-packaging tests.

**Modify**
- `app/ai/health.py`
- `app/ai/base.py`
- `app/config.py`
- `app/ai/router.py`
- `tests/test_provider_order.py`
- `.env.example`
- `.gitignore`
- `docker-compose.yml`
- `Dockerfile` — include `scripts/` so the probe can run from the private Compose network.
- `README.md`
- `.github/workflows/audit.yml`

**Do not modify unless a failing test proves it necessary**
- `app/ai/synthesis.py`
- `app/ai/bai.py`
- `app/ai/multimodal.py`
- search/Crawl4AI modules

---

### Task 1: Add generic temporary auth-failure cooldown

**Files:**
- Create: `tests/test_aurora_health.py`
- Modify: `app/ai/health.py`
- Modify: `app/ai/base.py`

**Interfaces:**
- Produces `ProviderHealth(auth_failure_cooldown_sec: float | None = None)`.
- Produces `OpenAICompatProvider(..., auth_failure_cooldown_sec: float | None = None)`.
- `None` preserves permanent 401/403 disable; a number means temporary cooldown.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_aurora_health.py
from __future__ import annotations

import unittest
from unittest.mock import patch

from app.ai.health import ProviderHealth


class ProviderAuthHealthPolicyTests(unittest.TestCase):
    def test_default_401_still_disables_permanently(self) -> None:
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

Expected: FAIL because the new policy field does not exist.

- [ ] **Step 3: Implement the policy**

In `app/ai/health.py` add the field first in the dataclass:

```python
auth_failure_cooldown_sec: float | None = None
```

Replace the current 401/403 branch with:

```python
if status_code in (401, 403):
    if self.auth_failure_cooldown_sec is None:
        self.disabled = True
    else:
        delay = retry_after if retry_after is not None else self.auth_failure_cooldown_sec
        self.cooldown_until = now + max(0.0, delay)
    return
```

In `OpenAICompatProvider.__init__()` add final parameter:

```python
auth_failure_cooldown_sec: float | None = None,
```

and initialize:

```python
self.health = ProviderHealth(auth_failure_cooldown_sec=auth_failure_cooldown_sec)
```

Do not add provider-name checks.

- [ ] **Step 4: Run GREEN**

```bash
python -m unittest tests.test_aurora_health tests.test_bai_provider tests.test_provider_metadata -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/ai/health.py app/ai/base.py tests/test_aurora_health.py
git commit -m "feat: support temporary provider auth cooldown"
```

---

### Task 2: Add Aurora settings, factory, and router slot

**Files:**
- Create: `app/ai/aurora.py`
- Create: `tests/test_aurora_routing.py`
- Create: `tests/test_aurora_provider.py`
- Modify: `app/config.py`
- Modify: `app/ai/router.py`
- Modify: `tests/test_provider_order.py`

**Interfaces:**
- Produces `make_aurora_provider(settings: Settings, *, name: str = "aurora") -> OpenAICompatProvider`.
- Settings: `aurora_base_url`, `aurora_api_key`, `aurora_model`, `aurora_request_timeout_sec`.
- Factory passes `auth_failure_cooldown_sec=60.0` and text-only capability.

- [ ] **Step 1: Write failing factory and routing tests**

```python
# tests/test_aurora_provider.py
from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

import app.ai.aurora as aurora
from app.config import Settings


class AuroraProviderFactoryTests(unittest.TestCase):
    def test_default_settings_match_private_sidecar(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.aurora_base_url, "http://aurora:8080/v1")
        self.assertEqual(settings.aurora_api_key, "")
        self.assertEqual(settings.aurora_model, "auto")
        self.assertEqual(settings.aurora_request_timeout_sec, 90.0)

    def test_factory_is_text_only_and_uses_temporary_auth_policy(self) -> None:
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

```python
# tests/test_aurora_routing.py
from __future__ import annotations

import asyncio
import unittest

from app.ai.router import build_provider_router
from app.config import Settings


async def _close_router(router) -> None:
    for provider in router.providers:
        await provider.aclose()


class AuroraRoutingTests(unittest.TestCase):
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
            self.assertEqual(settings.configured_provider_names, ["bai", "aurora", "gemini"])
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
```

Update `tests/test_provider_order.py` default text-order expectation to:

```python
["bai", "aurora", "gemini", "groq", "cloudflare", "openrouter"]
```

- [ ] **Step 2: Run RED**

```bash
python -m unittest tests.test_aurora_provider.AuroraProviderFactoryTests tests.test_aurora_routing tests.test_provider_order -v
```

Expected: FAIL because Aurora settings/factory/router slot do not exist.

- [ ] **Step 3: Add settings and configured-provider detection**

In `app/config.py` add:

```python
aurora_base_url: str = "http://aurora:8080/v1"
aurora_api_key: str = ""
aurora_model: str = "auto"
aurora_request_timeout_sec: float = Field(default=90.0, gt=0, allow_inf_nan=False)
```

Set current-baseline order:

```python
text_provider_order: str = "bai,aurora,gemini,groq,cloudflare,openrouter"
```

In `configured_provider_names` add:

```python
if self.aurora_base_url.strip() and self.aurora_api_key and self.aurora_model:
    available.add("aurora")
```

If latest accepted `main` removed later providers, keep the surviving order `bai,aurora` and do not restore deleted provider code.

- [ ] **Step 4: Create factory**

```python
# app/ai/aurora.py
"""Aurora ChatGPT Web gateway provider integration."""

from __future__ import annotations

from ..config import Settings
from .base import OpenAICompatProvider
from .capabilities import ProviderCapabilities

_AUTH_FAILURE_COOLDOWN_SEC = 60.0


def make_aurora_provider(settings: Settings, *, name: str = "aurora") -> OpenAICompatProvider:
    return OpenAICompatProvider(
        name=name,
        base_url=settings.aurora_base_url,
        api_key=settings.aurora_api_key,
        model=settings.aurora_model,
        timeout=settings.aurora_request_timeout_sec,
        capabilities=ProviderCapabilities(route="text", supports_vision=False, max_images=0),
        auth_failure_cooldown_sec=_AUTH_FAILURE_COOLDOWN_SEC,
    )
```

- [ ] **Step 5: Register factory in `build_provider_router()`**

Add:

```python
from .aurora import make_aurora_provider
```

and in text-provider construction:

```python
if (
    "aurora" in settings.text_provider_order_list
    and settings.aurora_base_url.strip()
    and settings.aurora_api_key
    and settings.aurora_model
):
    text["aurora"] = make_aurora_provider(settings)
```

Keep the existing order loop; do not create an Aurora-specific fallback branch.

- [ ] **Step 6: Run GREEN**

```bash
python -m unittest tests.test_aurora_provider.AuroraProviderFactoryTests tests.test_aurora_routing tests.test_provider_order -v
python -m unittest tests.test_free_routing tests.test_fresh_synthesis_routing -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/ai/aurora.py app/ai/router.py tests/test_aurora_provider.py tests/test_aurora_routing.py tests/test_provider_order.py
git commit -m "feat: route Aurora after B.AI"
```

---

### Task 3: Lock wire contract and full client-side tool loop

**Files:**
- Modify: `tests/test_aurora_provider.py`
- Modify only on demonstrated RED: `app/ai/base.py`, `app/ai/router.py`

**Interfaces:**
- No new runtime interface expected.
- Uses existing `POST /v1/chat/completions`, Bearer service auth, `choices[0].message`, and OpenAI-format `tool_calls`.

- [ ] **Step 1: Extend the import block at the top of `tests/test_aurora_provider.py`**

Add these imports to the existing top import section, not after class definitions:

```python
import json
import httpx

from app.ai.base import ProviderError
from app.ai.router import AIProviderRouter
```

- [ ] **Step 2: Add exact mock helper and wire/tool tests**

Append below the factory tests:

```python
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


async def _mocked_provider(handler) -> object:
    settings = SimpleNamespace(
        aurora_base_url="http://aurora:8080/v1",
        aurora_api_key="internal-secret",
        aurora_model="auto",
        aurora_request_timeout_sec=90.0,
    )
    provider = aurora.make_aurora_provider(settings)
    await provider.aclose()
    provider._client = httpx.AsyncClient(
        base_url="http://aurora:8080/v1/",
        headers={"Authorization": "Bearer internal-secret"},
        transport=httpx.MockTransport(handler),
    )
    return provider


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

        provider = await _mocked_provider(respond)
        try:
            result = await provider.chat([{"role": "user", "content": "hello"}], [_search_tool()])
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
                json={"choices": [{"message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_aurora",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"query":"aurora"}'},
                    }],
                }}]},
                request=request,
            )

        provider = await _mocked_provider(respond)
        try:
            result = await provider.chat([{"role": "user", "content": "search"}], [_search_tool()])
        finally:
            await provider.aclose()

        self.assertEqual(result.tool_calls[0].id, "call_aurora")
        self.assertEqual(result.tool_calls[0].name, "web_search")
        self.assertEqual(result.tool_calls[0].arguments, {"query": "aurora"})

    async def test_full_tool_round_trip_stays_in_existing_router_loop(self) -> None:
        requests: list[dict] = []
        executed: list[tuple[str, dict]] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            if len(requests) == 1:
                body = {"choices": [{"message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_aurora",
                        "type": "function",
                        "function": {"name": "web_search", "arguments": '{"query":"aurora"}'},
                    }],
                }}]}
            else:
                body = {"choices": [{"message": {"role": "assistant", "content": "final from aurora"}}]}
            return httpx.Response(200, json=body, request=request)

        async def tool_executor(name: str, arguments: dict) -> str:
            executed.append((name, arguments))
            return "search result"

        provider = await _mocked_provider(respond)
        router = AIProviderRouter([provider], max_tool_rounds=2)
        try:
            answer, provider_name = await router.complete(
                [{"role": "user", "content": "find aurora"}],
                [_search_tool()],
                tool_executor,
            )
        finally:
            await provider.aclose()

        self.assertEqual(answer, "final from aurora")
        self.assertEqual(provider_name, "aurora")
        self.assertEqual(executed, [("web_search", {"query": "aurora"})])
        self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
        self.assertEqual(requests[1]["messages"][-1]["tool_call_id"], "call_aurora")

    async def test_unknown_raw_tool_markup_is_rejected(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {
                    "role": "assistant",
                    "content": "<tool_call>unknown_tool<arg_key>x</arg_key><arg_value>1</arg_value></tool_call>",
                }}]},
                request=request,
            )

        provider = await _mocked_provider(respond)
        try:
            with self.assertRaises(ProviderError):
                await provider.chat([{"role": "user", "content": "test"}], [_search_tool()])
        finally:
            await provider.aclose()
```

- [ ] **Step 3: Run compatibility tests**

```bash
python -m unittest tests.test_aurora_provider.AuroraWireContractTests -v
python -m unittest tests.test_tool_fallback_recovery tests.test_fresh_synthesis_routing -v
```

Expected: PASS using generic provider/router code. If a test is RED, make only the minimal generic compatibility change proven by that failure and rerun these commands.

- [ ] **Step 4: Commit**

```bash
git add tests/test_aurora_provider.py
git add -u app/ai/base.py app/ai/router.py
git commit -m "test: lock Aurora chat and tool contract"
```

`git add -u` stages runtime files only if they actually changed.

---

### Task 4: Add private pinned sidecar and deployment security boundary

**Files:**
- Create: `tests/test_aurora_compose.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `.gitignore`
- Modify: `README.md`

**Interfaces:**
- Compose profile `aurora`.
- Bot endpoint `http://aurora:8080/v1`.
- Deploy vars `AURORA_IMAGE`, `AURORA_CREDENTIAL_FILE`, `AURORA_CREDENTIAL_TARGET`.
- Default mount `./aurora/session_tokens.txt` -> `/session_tokens.txt`, read-only.

- [ ] **Step 1: Write failing static contract tests**

```python
# tests/test_aurora_compose.py
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

    def test_aurora_is_profiled_private_and_pinned(self) -> None:
        self.assertIn('profiles: ["aurora"]', self.block)
        self.assertIn("ghcr.io/aurora-develop/aurora:v2.6.3", self.block)
        self.assertNotIn("ports:", self.block)
        self.assertNotIn("8080:8080", self.block)

    def test_risky_gateway_features_are_disabled(self) -> None:
        self.assertIn('FREE_ACCOUNTS: "false"', self.block)
        self.assertIn('ENABLE_EXTERNAL_TOKEN: "false"', self.block)
        self.assertIn('ENABLE_HISTORY: "false"', self.block)
        self.assertIn('TOOL_CALLING_ENABLED: "true"', self.block)
        self.assertIn('STREAM_MODE: "false"', self.block)
        self.assertIn('REFUSAL_RETRIES: "3"', self.block)

    def test_chatgpt_credentials_are_read_only_files_not_env_values(self) -> None:
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

Expected: FAIL because sidecar/deploy settings do not exist.

- [ ] **Step 3: Add service/deploy settings to `.env.example`**

```dotenv
# ============ Aurora / ChatGPT Web fallback ============
# Internal Aurora service key only. Never put ChatGPT session/access/refresh tokens here.
AURORA_BASE_URL=http://aurora:8080/v1
AURORA_API_KEY=
AURORA_MODEL=auto
AURORA_REQUEST_TIMEOUT_SEC=90.0
AURORA_IMAGE=ghcr.io/aurora-develop/aurora:v2.6.3
AURORA_CREDENTIAL_FILE=./aurora/session_tokens.txt
AURORA_CREDENTIAL_TARGET=/session_tokens.txt
```

- [ ] **Step 4: Ignore credential files**

```gitignore
# Aurora ChatGPT Web credentials — never commit.
aurora/session_tokens.txt
aurora/refresh_tokens.txt
aurora/access_tokens.txt
```

- [ ] **Step 5: Add private Compose service**

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

Do not add `ports:` or hard bot `depends_on: aurora`.

- [ ] **Step 6: Add exact README operator guidance**

Document:

```bash
mkdir -p aurora
chmod 700 aurora
# create aurora/session_tokens.txt locally, one token per line
chmod 600 aurora/session_tokens.txt
docker compose --profile aurora up -d aurora
```

Refresh-token alternative:

```dotenv
AURORA_CREDENTIAL_FILE=./aurora/refresh_tokens.txt
AURORA_CREDENTIAL_TARGET=/refresh_tokens.txt
```

Also state: Aurora is an unofficial ChatGPT Web gateway that may break after upstream web changes; `AURORA_API_KEY` is the internal service key, not a ChatGPT token; rollout starts as B.AI fallback; rollback removes `aurora` from `TEXT_PROVIDER_ORDER` and stops the profile.

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

Expected: PASS without a real token or starting Aurora.

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml .env.example .gitignore README.md tests/test_aurora_compose.py
git commit -m "feat: add private Aurora sidecar"
```

---

### Task 5: Add operator-safe smoke probe inside the bot image

**Files:**
- Create: `scripts/probe_aurora.py`
- Create: `tests/test_aurora_probe.py`
- Modify: `Dockerfile`

**Interfaces:**
- Reads only `AURORA_BASE_URL`, `AURORA_API_KEY`, `AURORA_MODEL`.
- Modes `models`, `chat`, `tool`, `all`.
- Never reads ChatGPT token files.
- Tool mode forces `echo_probe` but never executes it.
- `scripts/` is copied into `/app/scripts` so the probe runs in the existing private Compose network.

- [ ] **Step 1: Write failing helper/packaging tests**

```python
# tests/test_aurora_probe.py
from __future__ import annotations

from pathlib import Path
import unittest

from scripts.probe_aurora import _bounded_redacted, _extract_chat_text, _extract_tool_call


class AuroraProbeHelperTests(unittest.TestCase):
    def test_output_is_bounded_and_secret_is_redacted(self) -> None:
        secret = "super-secret-key"
        result = _bounded_redacted(secret + ("x" * 1000), secret=secret, limit=120)
        self.assertNotIn(secret, result)
        self.assertLessEqual(len(result), 120)

    def test_chat_text_is_extracted(self) -> None:
        self.assertEqual(
            _extract_chat_text({"choices": [{"message": {"content": "ok"}}]}),
            "ok",
        )

    def test_tool_call_requires_expected_function(self) -> None:
        call = _extract_tool_call({
            "choices": [{"message": {"tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "echo_probe", "arguments": '{"value":"aurora"}'},
            }]}}]
        })
        self.assertEqual(call["function"]["name"], "echo_probe")

    def test_production_image_copies_scripts(self) -> None:
        dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY scripts ./scripts", dockerfile)
```

- [ ] **Step 2: Run RED**

```bash
python -m unittest tests.test_aurora_probe -v
```

Expected: FAIL because the script does not exist and Dockerfile does not copy scripts.

- [ ] **Step 3: Create the complete probe**

```python
# scripts/probe_aurora.py
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
        raise ValueError("Aurora did not return echo_probe")
    arguments = json.loads(function.get("arguments") or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("Aurora tool arguments are not an object")
    return call


def _config() -> tuple[str, str, str]:
    base_url = os.getenv("AURORA_BASE_URL", "http://aurora:8080/v1").rstrip("/")
    api_key = os.getenv("AURORA_API_KEY", "").strip()
    model = os.getenv("AURORA_MODEL", "auto").strip() or "auto"
    if not api_key:
        raise ValueError("AURORA_API_KEY is required")
    return base_url, api_key, model


def _probe_models(client: httpx.Client) -> None:
    response = client.get("models")
    response.raise_for_status()
    data = response.json()
    models = data.get("data") if isinstance(data, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("Aurora /v1/models returned no models")
    print(f"models: ok ({len(models)})")


def _probe_chat(client: httpx.Client, model: str, api_key: str) -> None:
    response = client.post(
        "chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: aurora-ok"}],
        },
    )
    response.raise_for_status()
    print("chat:", _bounded_redacted(_extract_chat_text(response.json()), secret=api_key))


def _probe_tool(client: httpx.Client, model: str) -> None:
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
            "tool_choice": {"type": "function", "function": {"name": "echo_probe"}},
        },
    )
    response.raise_for_status()
    call = _extract_tool_call(response.json())
    print("tool: ok", str(call.get("id") or ""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("models", "chat", "tool", "all"), default="all")
    args = parser.parse_args()
    api_key = ""
    try:
        base_url, api_key, model = _config()
        with httpx.Client(
            base_url=base_url + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        ) as client:
            if args.mode in ("models", "all"):
                _probe_models(client)
            if args.mode in ("chat", "all"):
                _probe_chat(client, model, api_key)
            if args.mode in ("tool", "all"):
                _probe_tool(client, model)
        return 0
    except (httpx.HTTPError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        print("aurora probe failed:", _bounded_redacted(str(exc), secret=api_key), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Package scripts in Dockerfile**

Immediately after the existing `COPY app ./app` line add:

```dockerfile
COPY scripts ./scripts
```

Do not change the container command or user.

- [ ] **Step 5: Run GREEN**

```bash
python -m unittest tests.test_aurora_probe -v
python -m compileall -q scripts/probe_aurora.py
```

Expected: PASS without network access.

- [ ] **Step 6: Commit**

```bash
git add scripts/probe_aurora.py tests/test_aurora_probe.py Dockerfile
git commit -m "feat: add Aurora smoke probe"
```

---

### Task 6: Add explicit CI coverage and complete offline verification

**Files:**
- Modify: `.github/workflows/audit.yml`
- Modify only if verification exposes a real defect: directly responsible files.

**Interfaces:**
- CI stays offline for Aurora/ChatGPT.
- Matrix stays Python 3.11/3.12.
- Existing dependency audit and bot image build stay enabled.

- [ ] **Step 1: Add targeted Aurora CI step**

Before general regression tests add:

```yaml
      - name: Targeted Aurora tests
        run: |
          python -m unittest discover -s tests -p 'test_aurora*.py' -v
```

In Python 3.12 Compose validation use:

```yaml
          cp .env.example .env
          mkdir -p aurora
          touch aurora/session_tokens.txt
          docker compose --profile aurora config --quiet
```

Do not start Aurora in CI.

- [ ] **Step 2: Run targeted tests**

```bash
python -m unittest discover -s tests -p 'test_aurora*.py' -v
python -m unittest tests.test_provider_order tests.test_bai_provider tests.test_free_routing -v
python -m unittest tests.test_tool_fallback_recovery tests.test_fresh_synthesis_routing tests.test_router_concurrency -v
```

Expected: PASS.

- [ ] **Step 3: Run full regressions**

```bash
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: PASS.

- [ ] **Step 4: Run static checks**

```bash
python -m ruff check .
python -m compileall -q app tests scripts
```

Expected: PASS.

- [ ] **Step 5: Validate Compose and production image**

```bash
cp .env.example .env
mkdir -p aurora
touch aurora/session_tokens.txt
docker compose --profile aurora config --quiet
docker build --tag fcai-audit:aurora .
rm -f .env aurora/session_tokens.txt
rmdir aurora 2>/dev/null || true
```

Expected: PASS.

- [ ] **Step 6: Audit complete diff**

```bash
git fetch origin main
BASE_SHA="$(git merge-base HEAD origin/main)"
git diff --check
git status --short
git diff "$BASE_SHA"...HEAD -- app tests scripts Dockerfile docker-compose.yml .env.example .gitignore README.md .github/workflows/audit.yml
```

Reject any diff containing:

```text
real ChatGPT/Aurora secrets
Aurora host ports
ghcr.io/aurora-develop/aurora:latest as default
ENABLE_EXTERNAL_TOKEN=true
ENABLE_HISTORY=true
FREE_ACCOUNTS=true
provider-name special cases in health/router
B.AI model/tool-budget/Fresh Qwen changes
resurrection of providers removed by an accepted concurrent cleanup
```

Fix any finding and rerun Steps 2-5.

- [ ] **Step 7: Commit CI changes**

```bash
git add .github/workflows/audit.yml
git commit -m "ci: cover Aurora integration"
```

- [ ] **Step 8: Push and verify Actions**

Push `feat/aurora-chatgpt-web-integration`, open a PR to `main`, verify Python 3.11 and 3.12 jobs pass, and leave the PR unmerged.

---

### Task 7: Run credentialed deployment canary after CI passes

**Files:**
- No committed changes unless the canary exposes a reproducible defect; add a failing test before fixing such a defect.

**Interfaces:**
- Uses local `aurora/session_tokens.txt` or refresh-token alternative.
- Uses `AURORA_API_KEY` only as bot-to-Aurora service auth.

- [ ] **Step 1: Prepare local credentials**

```bash
mkdir -p aurora
chmod 700 aurora
# create aurora/session_tokens.txt locally, one valid token per line
chmod 600 aurora/session_tokens.txt
```

Generate a distinct strong `AURORA_API_KEY` in deployment `.env`; never commit it.

- [ ] **Step 2: Start Aurora only**

```bash
docker compose --profile aurora up -d aurora
```

Verify no host port 8080 is published.

- [ ] **Step 3: Run probes from the private Compose network**

Use the bot image, which now contains `scripts/`:

```bash
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode models
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode chat
docker compose run --rm --no-deps bot python scripts/probe_aurora.py --mode tool
```

Required: all exit 0; tool mode returns a parseable `echo_probe` call and never executes it.

- [ ] **Step 4: Enable canary order**

Current baseline:

```dotenv
TEXT_PROVIDER_ORDER=bai,aurora,gemini,groq,cloudflare,openrouter
```

After accepted provider cleanup:

```dotenv
TEXT_PROVIDER_ORDER=bai,aurora
```

Verify ordinary B.AI-success requests still select B.AI.

- [ ] **Step 5: Exercise one controlled fallback and one tool round-trip**

In staging/test configuration induce one controlled B.AI failure without changing production B.AI model settings. Verify Aurora answers, fallback count increments, no ChatGPT token appears in logs, and one safe read/search tool completes:

```text
Aurora tool_calls -> bot executes tool -> role=tool -> Aurora final text
```

- [ ] **Step 6: Validate temporary auth recovery in staging**

Cause one staging Aurora auth failure, observe 60-second cooldown, restore valid Aurora/session state, and verify Aurora becomes eligible again without bot restart. Do not repeatedly hammer a real account.

- [ ] **Step 7: Record non-secret canary result on the PR**

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

If canary fails, do not merge; reproduce with an offline failing test where possible, fix with TDD, and rerun the affected verification gate.

---

## Final Acceptance Checklist

- [ ] Aurora is optional and text-only.
- [ ] B.AI stays first; Aurora is next when configured.
- [ ] Existing `OpenAICompatProvider`/router are reused.
- [ ] Python settings contain no ChatGPT tokens.
- [ ] Aurora has no host port.
- [ ] Default image is pinned to `v2.6.3`.
- [ ] Credential mount is read-only and Git-ignored.
- [ ] `FREE_ACCOUNTS=false`.
- [ ] `ENABLE_EXTERNAL_TOKEN=false`.
- [ ] `ENABLE_HISTORY=false`.
- [ ] `TOOL_CALLING_ENABLED=true`.
- [ ] `STREAM_MODE=false`.
- [ ] Existing providers still disable permanently on 401/403.
- [ ] Aurora 401/403 cools down temporarily and recovers eligibility.
- [ ] 429/5xx behavior is unchanged.
- [ ] Text parsing, OpenAI-format tool parsing, and full tool round-trip pass.
- [ ] Malformed/unknown tool markup is rejected.
- [ ] Fresh Qwen Synthesis tests pass unchanged.
- [ ] Tool-budget/parallelism constants are unchanged.
- [ ] Search/Crawl4AI behavior is unchanged.
- [ ] No removed provider is resurrected after rebase.
- [ ] All targeted and full offline tests pass.
- [ ] Ruff and `compileall` pass.
- [ ] `docker compose --profile aurora config --quiet` passes.
- [ ] Production bot image builds and contains `scripts/probe_aurora.py`.
- [ ] GitHub Actions passes on Python 3.11 and 3.12.
- [ ] Credentialed models/chat/tool canary passes before merge.
- [ ] PR remains unmerged until explicit user approval.
