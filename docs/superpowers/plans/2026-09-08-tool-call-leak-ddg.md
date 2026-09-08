# Tool-call Leak and DDG Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent textual tool-call markup from ever reaching Telegram users and disable the currently broken DuckDuckGo SearXNG engine path.

**Architecture:** Keep tool-call handling inside the AI router, where structured tools are already orchestrated. Convert the exact observed text-encoded tool-call format into normal `ToolCall` objects when tools are available, reject malformed/internal tool markup instead of returning it as final text, and avoid retrying `tool_use_failed` responses in plain mode. For SearXNG, remove the broken default `duckduckgo` engine and leave `duckduckgo web` disabled until its upstream fix is merged.

**Tech Stack:** Python 3, unittest, httpx MockTransport, SearXNG YAML configuration.

**Spec:** Production incident reported in chat on 2026-09-08: Telegram answers intermittently contain `<tool_call>web_search ...</tool_call>` and SearXNG `duckduckgo web` raises `KeyError: results`.

## Global Constraints

- Preserve the existing OpenAI-compatible provider API.
- Do not execute unknown tool names parsed from assistant text.
- Do not silently strip malformed tool markup and return a potentially incomplete answer.
- Keep `SEARCH_BACKEND=auto` fallback behavior unchanged.
- Do not enable `duckduckgo web` while upstream SearXNG PR #6658 remains unmerged.

---

### Task 1: Regression coverage for textual tool-call leakage

**Files:**
- Modify: `tests/test_free_routing.py`
- Modify: `app/ai/router.py`
- Modify: `app/ai/base.py`

**Interfaces:**
- Consumes: `ChatResponse`, `ToolCall`, `AIProviderRouter.complete()`.
- Produces: router behavior that converts an exact text-only `<tool_call>...` response into a normal tool execution when the tool is allowed, and rejects internal tool markup from final answers.

- [ ] **Step 1: Write the failing test**

Add an async regression test whose fake provider first returns exactly:

```text
<tool_call>web_search
<arg_key>query</arg_key>
<arg_value>Việt Nam công ty khai thác xuất khẩu đất hiếm 2024 2025</arg_value>
</tool_call>
```

The test must assert that `web_search` is executed once and the router returns the provider's second response, not the markup.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python -m unittest tests.test_free_routing.ProviderMetadataTests.test_text_encoded_tool_call_is_executed_not_leaked -v
```

Expected: FAIL because the current router returns the markup as final text and never calls the tool executor.

- [ ] **Step 3: Implement the minimal parser/guard**

Implement a narrow parser for whole-response blocks using the observed tags (`tool_call`, `arg_key`, `arg_value`). Only accept tool names present in the active tool schema. If internal tool markup is present but cannot be safely converted, raise `ProviderError` so another provider can be tried instead of leaking it.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same unittest command and expect PASS.

- [ ] **Step 5: Add retry regression coverage**

Add a test where provider A returns HTTP 400 `tool_use_failed` while tools are active and provider B succeeds with tools. Assert provider A is not retried without tools and provider B is selected.

- [ ] **Step 6: Implement the minimal retry change**

Change OpenAI-compatible error classification so `tool_use_failed` / `failed_generation` does not set `retry_without_tools=True`; fall through to the next capable provider with tool support.

- [ ] **Step 7: Run routing tests**

Run:

```bash
python -m unittest tests.test_free_routing -v
```

Expected: PASS.

### Task 2: Disable broken DuckDuckGo SearXNG route

**Files:**
- Modify: `tests/test_searxng_config.py`
- Modify: `searxng/settings.example.yml`
- Modify: `DEPLOY_SEARXNG_VPS.md`

**Interfaces:**
- Consumes: SearXNG `use_default_settings.engines.remove` merge semantics.
- Produces: sample deployment config that excludes `duckduckgo` and does not enable `duckduckgo web`.

- [ ] **Step 1: Write failing config regression test**

Assert that the exact engine name `duckduckgo` appears under the removal list and that the sample does not contain an enabled `duckduckgo web` block.

- [ ] **Step 2: Run focused config test and verify RED**

Run:

```bash
python -m unittest tests.test_searxng_config -v
```

Expected: FAIL because `duckduckgo` is not currently removed.

- [ ] **Step 3: Update sample configuration**

Add `duckduckgo` to the removal list with a comment explaining that the HTML engine currently CAPTCHA-blocks datacenter traffic. Keep `duckduckgo web` at upstream default-disabled state; do not add an enabling override.

- [ ] **Step 4: Update deployment documentation**

Document that production `searxng/settings.yml` must be updated manually from the sample and the SearXNG container recreated. Mention that `duckduckgo web` currently has an upstream `KeyError`/bot-detection issue and should remain disabled until fixed upstream.

- [ ] **Step 5: Run config tests and verify GREEN**

Run the same unittest command and expect PASS.

### Task 3: Verification and review

**Files:**
- Review all changed files.

**Interfaces:**
- Produces: verified branch ready for PR review.

- [ ] **Step 1: Run the repository test entrypoint**

Run:

```bash
python tests/run_tests.py
```

Expected: all tests pass.

- [ ] **Step 2: Inspect diff for accidental behavior changes**

Confirm no changes to provider ordering, search fallback semantics, Telegram formatting, or image search behavior beyond the incident fixes.

- [ ] **Step 3: Open a pull request**

Create a PR from `fix/tool-call-leak-ddg` to `main` summarizing root cause, regression tests, and VPS deployment note.
