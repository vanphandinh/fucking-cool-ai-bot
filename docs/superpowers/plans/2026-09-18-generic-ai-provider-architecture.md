# Generic AI Provider Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Refactor AI integration so OpenAI-compatible providers are configuration-only additions, while new native protocols require only one protocol driver and no router/retry/health/orchestrator changes.

**Architecture:** Preserve the existing AIProviderRouter as the deep module for routing, retry, health, fallback, tool execution, and portable state. Move provider-family variability into declarative ProviderProfile data, immutable TargetSpec metadata, a generic target builder, and protocol-specific drivers. Complete the migration in two milestones: Milestone A makes construction/config/recovery generic; Milestone B removes OpenAI-shaped message dictionaries from the router/orchestrator seam.

**Tech Stack:** Python 3.11/3.12, dataclasses, typing.Protocol, pydantic-settings, stdlib tomllib, httpx, unittest, ruff.

**Spec:** \`docs/superpowers/specs/2026-09-18-generic-ai-provider-architecture-design.md\`

## Global Constraints

- Preserve current text/vision provider ordering defaults: \`chainnode,xkiro\`.
- Preserve deterministic target ordering: model-major, then credential.
- Preserve existing retry, recovery-hop, scoped-health, tool-budget, tool-portability, and provider-metadata isolation semantics.
- Drivers perform exactly one network attempt per \`chat()\`; retry/fallback remains owned by the router.
- Raw credentials must never appear in target identity, repr output, logs, status output, or exceptions.
- Preserve the existing maximum of 64 enabled AI targets.
- Keep Python 3.11 and 3.12 support.
- Do not add a runtime dependency when the Python standard library is sufficient.
- Do not add dynamic external Python plugin discovery in this implementation.
- Do not query provider model catalogs during normal startup or request handling.
- Each task must follow red-green-refactor and end with an independently testable commit.

---

## File Structure

### New runtime files

- \`config/ai-providers.toml\` — declarative provider catalog for provider family metadata, route capability defaults, and recovery overrides.
- \`app/ai/catalog.py\` — parse/validate catalog into immutable \`ProviderProfile\`, \`RouteProfile\`, and recovery override data.
- \`app/ai/runtime_config.py\` — generic provider runtime settings model and helpers for credentials/models/base URL/timeout.
- \`app/ai/target_builder.py\` — expand provider profiles into concrete model × credential targets.
- \`app/ai/drivers/base.py\` — driver construction interface.
- \`app/ai/drivers/registry.py\` — protocol-driver registry keyed by driver id such as \`openai-chat\`.
- \`app/ai/drivers/openai_chat.py\` — OpenAI-compatible transport, serialization, response parsing, and normalized provider errors.
- \`app/ai/contracts.py\` — Milestone B canonical chat/message/tool/part types.

### Runtime files to modify

- \`app/ai/target.py\` — add explicit immutable \`TargetSpec\`; stop inferring target metadata through \`getattr\`.
- \`app/ai/provider.py\` — evolve provider protocol toward explicit \`spec\` metadata, then Milestone B \`ChatRequest\`.
- \`app/ai/router.py\` — consume explicit \`TargetSpec\`, generic target builder, and Milestone B canonical messages.
- \`app/ai/recovery.py\` — consume \`RecoveryPolicy\`; remove family-name branching.
- \`app/ai/base.py\` — shrink during Milestone A; delete OpenAI transport/parsing implementation after it moves into the driver.
- \`app/ai/multimodal.py\` — Milestone B only: produce canonical message parts instead of OpenAI image_url dictionaries.
- \`app/config.py\` — replace provider-family fields/properties/validation with generic nested provider runtime settings.
- \`app/core/orchestrator.py\` — Milestone B only: build canonical \`ChatRequest\` and \`ToolDefinition\`.
- \`app/main.py\` — no provider-family construction knowledge; continue using \`build_provider_router(settings)\`.

### Runtime files to delete after migration

- \`app/ai/chainnode.py\`
- \`app/ai/xkiro.py\`
- \`app/ai/registry.py\`

### Configuration/docs/tests to modify

- \`.env.example\`
- \`README.md\`
- \`docs/CHAINNODE.md\`
- \`docs/XKIRO.md\`
- \`scripts/probe_chainnode.py\` if present in the working tree
- \`scripts/probe_xkiro.py\`
- \`scripts/check_canonical_env_vocabulary.py\`
- provider-focused tests under \`tests/test_provider_*.py\`
- \`tests/test_vision.py\`
- \`tests/provider_fakes.py\`

---

# Milestone A — Configuration-Only OpenAI-Compatible Providers

### Task 1: Introduce declarative provider catalog parsing

**Files:**
- Create: \`config/ai-providers.toml\`
- Create: \`app/ai/catalog.py\`
- Create: \`tests/test_provider_catalog.py\`

**Interfaces:**
- Consumes: stdlib \`tomllib\`; existing \`ProviderCapabilities\`, recovery enums.
- Produces:
  - \`RouteProfile\`
  - \`RecoveryRule\`
  - \`ProviderProfile\`
  - \`ProviderCatalog\`
  - \`load_provider_catalog(path: Path | None = None) -> ProviderCatalog\`
  - \`ProviderCatalog.require(provider_id: str) -> ProviderProfile\`

- [ ] **Step 1: Write failing catalog parsing tests**

Add tests proving:

~~~python
def test_catalog_loads_chainnode_and_xkiro() -> None:
    catalog = load_provider_catalog()
    assert tuple(catalog.providers) == ("chainnode", "xkiro")
    assert catalog.require("chainnode").driver == "openai-chat"
    assert catalog.require("xkiro").routes["vision"].max_images == 1

def test_catalog_rejects_duplicate_provider_ids(tmp_path: Path) -> None:
    path = tmp_path / "providers.toml"
    path.write_text(
        """
[[providers]]
id = "dup"
driver = "openai-chat"

[[providers]]
id = "dup"
driver = "openai-chat"
""",
        encoding="utf-8",
    )
    with pytest_or_unittest_value_error("duplicate provider id"):
        load_provider_catalog(path)

def test_catalog_rejects_invalid_recovery_enum(tmp_path: Path) -> None:
    # catalog with scope="unknown"
    ...
~~~

Use the repository's existing \`unittest\` style rather than adding pytest.

- [ ] **Step 2: Run the focused test and verify red**

Run:

~~~bash
python -m unittest tests.test_provider_catalog -v
~~~

Expected: import failure for \`app.ai.catalog\`.

- [ ] **Step 3: Implement immutable catalog types and validation**

Use frozen dataclasses. The required provider profile shape is:

~~~python
@dataclass(frozen=True, slots=True)
class RouteProfile:
    enabled: bool = True
    supports_vision: bool = False
    max_images: int = 0

@dataclass(frozen=True, slots=True)
class RecoveryRule:
    status: int
    action: RecoveryAction
    scope: HealthScope
    effect: HealthEffect

@dataclass(frozen=True, slots=True)
class ProviderProfile:
    id: str
    driver: str
    default_base_url: str
    default_timeout_sec: float
    routes: Mapping[str, RouteProfile]
    recovery_rules: tuple[RecoveryRule, ...] = ()
    driver_options: Mapping[str, object] = field(default_factory=dict)
~~~

Validation requirements:
- ids are normalized lowercase and non-empty;
- only \`text\` and \`vision\` routes are accepted;
- vision \`max_images >= 1\` when \`supports_vision=True\`;
- text route cannot claim \`supports_vision=True\`;
- duplicate status override within one provider is rejected;
- timeout must be finite and > 0;
- raw secrets do not belong in this file or any parsed profile.

- [ ] **Step 4: Add initial Chainnode/xKiro catalog data**

\`config/ai-providers.toml\` must encode:
- Chainnode driver \`openai-chat\`, default base URL \`https://dn.chainno.de/v1\`, timeout 60, text enabled, vision enabled/max_images 1, no recovery overrides.
- xKiro driver \`openai-chat\`, default base URL \`https://api.xkiro.com/v1\`, timeout 60, text enabled, vision enabled/max_images 1, overrides:
  - 402 -> rotate_target / credential / disable
  - 403 -> rotate_target / entitlement / disable
  - 429 -> rotate_target / credential / cooldown

- [ ] **Step 5: Run focused tests and lint**

~~~bash
python -m unittest tests.test_provider_catalog -v
python -m ruff check app/ai/catalog.py tests/test_provider_catalog.py
~~~

Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add config/ai-providers.toml app/ai/catalog.py tests/test_provider_catalog.py
git commit -m "feat: add declarative AI provider catalog"
~~~

---

### Task 2: Replace provider-specific Settings fields with generic runtime settings

**Files:**
- Create: \`app/ai/runtime_config.py\`
- Modify: \`app/config.py\`
- Modify: \`.env.example\`
- Modify: \`tests/test_provider_env_contract.py\`
- Modify: \`tests/test_config_regressions.py\`
- Modify: \`tests/test_canonical_env_vocabulary.py\`
- Modify: \`scripts/check_canonical_env_vocabulary.py\`

**Interfaces:**
- Consumes: \`ProviderCatalog\` from Task 1.
- Produces:
  - \`ProviderRuntimeSettings\`
  - \`provider_runtime(settings: Settings, provider_id: str) -> ProviderRuntimeSettings\`
  - \`ProviderRuntimeSettings.api_keys_list\`
  - \`ProviderRuntimeSettings.text_models_list\`
  - \`ProviderRuntimeSettings.vision_models_list\`
  - \`ProviderRuntimeSettings.resolve_base_url(profile)\`
  - \`ProviderRuntimeSettings.resolve_timeout(profile)\`

- [ ] **Step 1: Write failing generic runtime-config tests**

Required tests:

~~~python
def test_nested_provider_env_is_loaded(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "AI_PROVIDERS__CHAINNODE__API_KEYS=KeyOne,KeyTwo",
                "AI_PROVIDERS__CHAINNODE__TEXT_MODELS=ModelA,ModelB",
                "AI_PROVIDERS__CHAINNODE__VISION_MODELS=VisionA",
                "AI_PROVIDERS__CHAINNODE__REQUEST_TIMEOUT_SEC=42",
            ]
        ),
        encoding="utf-8",
    )
    settings = Settings(_env_file=env)
    runtime = provider_runtime(settings, "chainnode")
    self.assertEqual(runtime.api_keys_list, ["KeyOne", "KeyTwo"])
    self.assertEqual(runtime.text_models_list, ["ModelA", "ModelB"])
    self.assertEqual(runtime.request_timeout_sec, 42.0)

def test_runtime_values_preserve_case_and_first_occurrence() -> None:
    ...

def test_provider_runtime_error_does_not_expose_api_key() -> None:
    ...
~~~

- [ ] **Step 2: Run focused tests and verify red**

~~~bash
python -m unittest tests.test_provider_env_contract tests.test_config_regressions -v
~~~

Expected: failures because \`Settings.ai_providers\` and \`provider_runtime\` do not exist.

- [ ] **Step 3: Implement \`ProviderRuntimeSettings\`**

Use:

~~~python
class ProviderRuntimeSettings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    api_keys: str = ""
    base_url: str = ""
    text_models: str = ""
    vision_models: str = ""
    request_timeout_sec: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
    )
~~~

Add parser properties using the current \`parse_unique_csv_preserve_case\`.

- [ ] **Step 4: Add generic nested provider mapping to Settings**

Use \`env_nested_delimiter="__"\` and:

~~~python
ai_providers: dict[str, ProviderRuntimeSettings] = Field(default_factory=dict)
~~~

Normalize provider keys to lowercase in a validator.

Keep \`TEXT_PROVIDER_ORDER\`, \`VISION_PROVIDER_ORDER\`, retry fields, and global vision fields unchanged.

- [ ] **Step 5: Replace provider-family validation**

The current \`_check_provider_and_timeout_config\` must:
- load catalog;
- for each provider referenced by text/vision order, require that the provider exists in catalog;
- only require models when that provider has non-empty credentials and participates in that route;
- count enabled concrete targets generically using route model count × credential count;
- enforce the 64-target bound;
- preserve all unrelated timeout/job/search validation.

Do not keep \`chainnode_*_list\` or \`xkiro_*_list\` properties after all callers are migrated in Task 4. Temporary compatibility properties are allowed only until Task 4 is green.

- [ ] **Step 6: Migrate \`.env.example\`**

Replace provider-specific variables with:

~~~text
AI_PROVIDERS__CHAINNODE__API_KEYS=
AI_PROVIDERS__CHAINNODE__BASE_URL=https://dn.chainno.de/v1
AI_PROVIDERS__CHAINNODE__TEXT_MODELS=cl/cline-free/deepseek-v4.1-flash
AI_PROVIDERS__CHAINNODE__VISION_MODELS=cl/cline-free/muse-spark-1.3-contributor
AI_PROVIDERS__CHAINNODE__REQUEST_TIMEOUT_SEC=60.0

AI_PROVIDERS__XKIRO__API_KEYS=
AI_PROVIDERS__XKIRO__BASE_URL=https://api.xkiro.com/v1
AI_PROVIDERS__XKIRO__TEXT_MODELS=
AI_PROVIDERS__XKIRO__VISION_MODELS=
AI_PROVIDERS__XKIRO__REQUEST_TIMEOUT_SEC=60.0
~~~

Keep provider order keys unchanged.

- [ ] **Step 7: Update canonical vocabulary enforcement**

Add old provider-specific env names to the retired vocabulary scanner only after all runtime/tests/docs migrations are complete. During this task, first add tests proving the new generic namespace is accepted and the old namespace will eventually be rejected.

- [ ] **Step 8: Run focused tests**

~~~bash
python -m unittest tests.test_provider_env_contract tests.test_config_regressions tests.test_canonical_env_vocabulary -v
python -m ruff check app/config.py app/ai/runtime_config.py tests/test_provider_env_contract.py
~~~

Expected: PASS.

- [ ] **Step 9: Commit**

~~~bash
git add app/config.py app/ai/runtime_config.py .env.example \
  tests/test_provider_env_contract.py tests/test_config_regressions.py \
  tests/test_canonical_env_vocabulary.py scripts/check_canonical_env_vocabulary.py
git commit -m "refactor: make provider runtime config generic"
~~~

---

### Task 3: Make concrete target metadata explicit with TargetSpec

**Files:**
- Modify: \`app/ai/target.py\`
- Modify: \`app/ai/provider.py\`
- Modify: \`tests/provider_fakes.py\`
- Modify: \`tests/test_provider_target_security.py\`
- Modify: \`tests/test_provider_routing.py\`

**Interfaces:**
- Consumes: \`ProviderProfile\`, \`ProviderCapabilities\`.
- Produces:
  - \`TargetSpec\`
  - \`AIProvider.spec: TargetSpec\`
  - \`provider_target_identity(provider) -> provider.spec.identity\`

- [ ] **Step 1: Write failing tests that require explicit target metadata**

Add:

~~~python
def test_provider_identity_comes_from_target_spec() -> None:
    provider = ScriptedProvider("demo", [])
    self.assertEqual(provider_target_identity(provider), provider.spec.identity)

def test_duplicate_target_detection_uses_spec_identity() -> None:
    ...

def test_target_spec_repr_never_contains_secret() -> None:
    ...
~~~

- [ ] **Step 2: Run focused tests and verify red**

~~~bash
python -m unittest tests.test_provider_target_security tests.test_provider_routing -v
~~~

- [ ] **Step 3: Implement TargetSpec**

Required shape:

~~~python
@dataclass(frozen=True, slots=True)
class TargetSpec:
    identity: ProviderTargetIdentity
    driver: str
    capabilities: ProviderCapabilities
    base_url: str
    request_timeout_sec: float
    recovery_policy: RecoveryPolicy
    driver_options: Mapping[str, object] = field(default_factory=dict)
~~~

At this task, if \`RecoveryPolicy\` is not yet introduced, define its immutable data type in \`recovery.py\` with default rules but do not change classifier behavior until Task 6.

- [ ] **Step 4: Change AIProvider protocol**

The temporary Milestone A protocol is:

~~~python
@runtime_checkable
class AIProvider(Protocol):
    spec: TargetSpec
    health: ProviderHealth
    supports_tools: bool

    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse: ...

    async def aclose(self) -> None: ...
~~~

Properties may delegate to \`spec.identity.family\`, \`spec.identity.model\`, and \`spec.capabilities\` for compatibility with the router during Milestone A.

- [ ] **Step 5: Upgrade test fakes**

\`ScriptedProvider\` must create a complete secret-free \`TargetSpec\` instead of setting \`credential_id\` and \`target_id\` dynamically.

- [ ] **Step 6: Remove getattr fallback identity behavior**

\`provider_target_identity(provider)\` must return \`provider.spec.identity\`. Missing \`spec\` should be a programming error, not silently default to \`cred-1\`.

- [ ] **Step 7: Run tests**

~~~bash
python -m unittest tests.test_provider_target_security tests.test_provider_routing tests.test_provider_registry -v
python -m ruff check app/ai/target.py app/ai/provider.py tests/provider_fakes.py
~~~

Expected: PASS.

- [ ] **Step 8: Commit**

~~~bash
git add app/ai/target.py app/ai/provider.py tests/provider_fakes.py \
  tests/test_provider_target_security.py tests/test_provider_routing.py
git commit -m "refactor: make AI target metadata explicit"
~~~

---

### Task 4: Extract OpenAI-compatible transport into a protocol driver

**Files:**
- Create: \`app/ai/drivers/__init__.py\`
- Create: \`app/ai/drivers/base.py\`
- Create: \`app/ai/drivers/openai_chat.py\`
- Create: \`app/ai/drivers/registry.py\`
- Modify: \`app/ai/base.py\`
- Modify: \`tests/test_ai_transport_resilience.py\`
- Modify: \`tests/test_provider_transport_retry.py\`
- Modify: \`tests/test_provider_api_key_normalization.py\`
- Modify: \`tests/test_vision.py\`

**Interfaces:**
- Consumes: \`TargetSpec\`, raw credential string.
- Produces:
  - \`Driver(Protocol)\`
  - \`OpenAIChatDriver\`
  - \`OpenAIChatTarget\`
  - \`DRIVERS: dict[str, Driver]\`
  - \`get_driver(driver_id: str) -> Driver\`

- [ ] **Step 1: Write failing driver-registry tests**

Create tests such as:

~~~python
def test_openai_chat_driver_is_registered() -> None:
    self.assertIsInstance(get_driver("openai-chat"), OpenAIChatDriver)

def test_unknown_driver_is_rejected() -> None:
    with self.assertRaisesRegex(ValueError, "unknown AI driver"):
        get_driver("missing")
~~~

- [ ] **Step 2: Write failing target-construction test**

Use an HTTPX MockTransport-compatible constructor seam and assert that the target:
- sends Authorization Bearer using the raw credential;
- uses \`TargetSpec.base_url\`;
- uses \`TargetSpec.identity.model\`;
- preserves current \`stream=False\` behavior;
- parses structured tool calls and current assistant replay metadata;
- redacts HTTP error bodies exactly as today.

- [ ] **Step 3: Run focused tests and verify red**

~~~bash
python -m unittest tests.test_ai_transport_resilience tests.test_provider_transport_retry tests.test_vision -v
~~~

- [ ] **Step 4: Move OpenAI code from base.py**

Move, without semantic change:
- \`_contains_internal_tool_markup\`
- structured/text tool-call parsing
- content normalization
- HTTP timeout constants
- \`OpenAICompatProvider\` implementation
- OpenAI-specific replay metadata field handling

into \`app/ai/drivers/openai_chat.py\`.

Keep shared domain errors/results in \`app/ai/base.py\` for Milestone A:
- \`ProviderError\`
- \`TransportFailureKind\`
- \`AllProvidersFailed\`
- \`NoCapableProvider\`
- \`ToolCall\`
- \`ChatResponse\`

Rename concrete runtime target to \`OpenAIChatTarget\`.

- [ ] **Step 5: Implement driver constructor**

~~~python
class Driver(Protocol):
    id: str

    def build_target(
        self,
        spec: TargetSpec,
        *,
        credential: str,
    ) -> AIProvider:
        ...
~~~

\`OpenAIChatDriver.build_target\` returns \`OpenAIChatTarget\`.

- [ ] **Step 6: Run focused regressions**

~~~bash
python -m unittest \
  tests.test_ai_transport_resilience \
  tests.test_provider_transport_retry \
  tests.test_provider_api_key_normalization \
  tests.test_vision -v
python -m ruff check app/ai/base.py app/ai/drivers
~~~

Expected: PASS with no behavior change.

- [ ] **Step 7: Commit**

~~~bash
git add app/ai/base.py app/ai/drivers tests/test_ai_transport_resilience.py \
  tests/test_provider_transport_retry.py tests/test_provider_api_key_normalization.py \
  tests/test_vision.py
git commit -m "refactor: extract OpenAI chat protocol driver"
~~~

---

### Task 5: Build targets generically from catalog + runtime configuration

**Files:**
- Create: \`app/ai/target_builder.py\`
- Create: \`tests/test_provider_target_builder.py\`
- Modify: \`app/ai/router.py: build_provider_router\`
- Modify: \`tests/test_provider_pool_factories.py\`
- Modify: \`tests/test_provider_registry.py\`

**Interfaces:**
- Consumes:
  - \`ProviderCatalog\`
  - \`ProviderRuntimeSettings\`
  - \`get_driver(driver_id)\`
- Produces:
  - \`build_provider_targets(settings: Settings, provider_ids: Sequence[str]) -> list[AIProvider]\`

- [ ] **Step 1: Write failing target-builder ordering tests**

Required assertions:

~~~python
def test_generic_builder_is_model_major_then_credential() -> None:
    settings = Settings(
        _env_file=None,
        ai_providers={
            "chainnode": {
                "api_keys": "KeyOne,KeyTwo",
                "text_models": "ModelA,ModelB",
                "vision_models": "VisionA",
            }
        },
        text_provider_order="chainnode",
        vision_provider_order="chainnode",
    )
    targets = build_provider_targets(settings, ["chainnode"])
    self.assertEqual(
        [
            (
                t.spec.identity.route,
                t.spec.identity.model,
                t.spec.identity.credential_id,
                t.spec.identity.target_id,
            )
            for t in targets
        ],
        [
            ("text", "ModelA", "cred-1", "chainnode:text:m1:c1"),
            ("text", "ModelA", "cred-2", "chainnode:text:m1:c2"),
            ("text", "ModelB", "cred-1", "chainnode:text:m2:c1"),
            ("text", "ModelB", "cred-2", "chainnode:text:m2:c2"),
            ("vision", "VisionA", "cred-1", "chainnode:vision:m1:c1"),
            ("vision", "VisionA", "cred-2", "chainnode:vision:m1:c2"),
        ],
    )
~~~

Also add:
- no credentials -> zero targets;
- provider absent from route order -> no targets for that route;
- unknown provider id -> ValueError before driver construction;
- unknown driver -> ValueError before HTTP client construction;
- target count > 64 -> validation failure;
- blank runtime base URL uses catalog default;
- runtime base URL overrides catalog default.

- [ ] **Step 2: Add the critical config-only provider test**

Create a temporary catalog with a third provider:

~~~toml
[[providers]]
id = "fixture"
driver = "openai-chat"
default_base_url = "https://fixture.example/v1"
default_timeout_sec = 15.0

[providers.routes.text]
enabled = true
~~~

The test must prove that adding this catalog entry plus \`Settings.ai_providers["fixture"]\` builds a routable target without importing or creating any \`app.ai.fixture\` Python module.

- [ ] **Step 3: Run focused tests and verify red**

~~~bash
python -m unittest tests.test_provider_target_builder -v
~~~

- [ ] **Step 4: Implement generic expansion**

For each provider id, route, model index, and credential index:
- build \`ProviderTargetIdentity\`;
- build \`TargetSpec\`;
- select driver by \`profile.driver\`;
- call \`driver.build_target(spec, credential=api_key)\`.

Do not branch on \`chainnode\` or \`xkiro\`.

- [ ] **Step 5: Change build_provider_router**

Replace:

~~~python
providers = build_registered_providers(settings, registered_names)
~~~

with:

~~~python
providers = build_provider_targets(settings, registered_names)
~~~

Keep all retry policy construction unchanged.

- [ ] **Step 6: Convert old factory tests**

Rename the conceptual target of \`test_provider_pool_factories.py\` from vendor factories to generic target-builder regressions, or delete redundant cases after equivalent coverage exists in \`test_provider_target_builder.py\`.

- [ ] **Step 7: Run routing/config regressions**

~~~bash
python -m unittest \
  tests.test_provider_target_builder \
  tests.test_provider_registry \
  tests.test_provider_routing \
  tests.test_provider_pool_factories \
  tests.test_vision -v
~~~

Expected: PASS.

- [ ] **Step 8: Commit**

~~~bash
git add app/ai/target_builder.py app/ai/router.py \
  tests/test_provider_target_builder.py tests/test_provider_registry.py \
  tests/test_provider_pool_factories.py
git commit -m "feat: build provider targets from generic profiles"
~~~

---

### Task 6: Make recovery policy provider-agnostic

**Files:**
- Modify: \`app/ai/recovery.py\`
- Modify: \`app/ai/router.py\`
- Modify: \`app/ai/target.py\`
- Modify: \`tests/test_provider_recovery_policy.py\`
- Modify: \`tests/test_provider_target_rotation.py\`
- Modify: \`tests/test_provider_recovery_scope_regressions.py\`

**Interfaces:**
- Consumes: \`TargetSpec.recovery_policy\`.
- Produces:
  - \`RecoveryPolicy\`
  - \`RecoveryPolicy.rule_for(status_code: int | None) -> RecoveryDecision | None\`
  - \`classify_recovery(spec: TargetSpec, error: ProviderError) -> RecoveryDecision\`

- [ ] **Step 1: Rewrite tests to describe policy rather than vendor names**

Required tests:
- default 429 -> model cooldown + rotate target;
- xKiro profile override 429 -> credential cooldown + rotate target;
- default 401 -> credential disable + rotate target;
- xKiro override 402 -> credential disable + rotate target;
- xKiro override 403 -> entitlement disable + rotate target;
- default 403 -> family disable + fallback;
- default 404 -> model disable + rotate target;
- 5xx -> family transient + fallback;
- source text of \`app/ai/recovery.py\` contains neither \`chainnode\` nor \`xkiro\`.

- [ ] **Step 2: Run recovery tests and verify red**

~~~bash
python -m unittest tests.test_provider_recovery_policy -v
~~~

- [ ] **Step 3: Implement default policy + exact-status overrides**

Suggested data model:

~~~python
@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    overrides: Mapping[int, RecoveryDecision] = field(default_factory=dict)

    def decision_for(
        self,
        error: ProviderError,
    ) -> RecoveryDecision:
        if error.transport_kind is not None:
            return TRANSPORT_RECORD_ONLY
        if error.status_code in self.overrides:
            return self.overrides[error.status_code]
        return default_recovery_decision(error)
~~~

- [ ] **Step 4: Change router callsite**

Replace:

~~~python
decision = classify_recovery(identity, exc)
~~~

with:

~~~python
decision = classify_recovery(provider.spec, exc)
~~~

No family-specific knowledge is permitted inside classifier code.

- [ ] **Step 5: Run full recovery-focused suite**

~~~bash
python -m unittest discover -s tests -p 'test_provider_recovery*.py' -v
python -m unittest tests.test_provider_target_rotation tests.test_provider_local_recovery_retry -v
~~~

Expected: PASS.

- [ ] **Step 6: Commit**

~~~bash
git add app/ai/recovery.py app/ai/router.py app/ai/target.py \
  tests/test_provider_recovery_policy.py tests/test_provider_target_rotation.py \
  tests/test_provider_recovery_scope_regressions.py
git commit -m "refactor: make provider recovery policy declarative"
~~~

---

### Task 7: Remove provider-family factory modules and registry

**Files:**
- Delete: \`app/ai/chainnode.py\`
- Delete: \`app/ai/xkiro.py\`
- Delete: \`app/ai/registry.py\`
- Modify: all tests importing those modules
- Modify: \`README.md\`
- Modify: \`docs/CHAINNODE.md\`
- Modify: \`docs/XKIRO.md\`
- Modify: \`scripts/probe_xkiro.py\`
- Modify: \`scripts/probe_chainnode.py\` if present
- Modify: \`scripts/check_canonical_env_vocabulary.py\`

**Interfaces:**
- Consumes: generic catalog/runtime/driver/target-builder path from Tasks 1-6.
- Produces: no new runtime interface; this task proves old seams are unnecessary.

- [ ] **Step 1: Add source-tree regression checks**

Tests must assert:
- no runtime import of \`app.ai.chainnode\`, \`app.ai.xkiro\`, or \`app.ai.registry\`;
- no \`build_chainnode_provider_slots\` / \`build_xkiro_provider_slots\`;
- no provider-specific Settings attribute names;
- old provider-specific env vocabulary is rejected by canonical scanner.

- [ ] **Step 2: Run scanner tests and verify red**

~~~bash
python -m unittest tests.test_canonical_env_vocabulary tests.test_provider_registry -v
~~~

- [ ] **Step 3: Migrate remaining tests to generic builder**

Where tests need concrete Chainnode/xKiro targets, use:
- \`Settings.ai_providers\`;
- \`build_provider_targets\`;
- \`build_provider_router\`.

Do not add test-only vendor factories.

- [ ] **Step 4: Update probes**

For xKiro probe:
- read \`AI_PROVIDERS__XKIRO__API_KEYS\`;
- read \`AI_PROVIDERS__XKIRO__BASE_URL\`;
- keep the current live qualification behavior unchanged.

Do the equivalent for Chainnode probe if retained.

- [ ] **Step 5: Delete old modules**

Delete all three files only after focused tests are green through the generic path.

- [ ] **Step 6: Update active docs**

README and provider docs must explain:
- provider runtime namespace;
- catalog location;
- OpenAI-compatible provider onboarding requires catalog + env only;
- xKiro recovery overrides are data, not router logic.

- [ ] **Step 7: Run Milestone A acceptance suite**

~~~bash
python -m ruff check .
python -m compileall -q app tests scripts
python scripts/check_canonical_env_vocabulary.py
python scripts/check_markdown_links.py
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
~~~

Expected: all pass.

- [ ] **Step 8: Commit**

~~~bash
git add -A
git commit -m "refactor: remove provider-family construction code"
~~~

---

# Milestone B — Protocol-Independent Canonical Chat Model

### Task 8: Define canonical chat/message/tool contracts

**Files:**
- Create: \`app/ai/contracts.py\`
- Create: \`tests/test_ai_contracts.py\`
- Modify: \`app/ai/provider.py\`

**Interfaces:**
- Produces:
  - \`TextPart\`
  - \`ImagePart\`
  - \`ToolCallPart\`
  - \`ToolResultPart\`
  - \`ChatMessage\`
  - \`ToolDefinition\`
  - \`ChatRequest\`
  - canonical \`ChatResponse\`

- [ ] **Step 1: Write failing contract tests**

Required invariants:
- \`ImagePart.data\` is bytes and repr does not contain base64;
- \`ToolDefinition.parameters\` is an object schema;
- \`ChatMessage.parts\` is immutable tuple;
- provider state is opaque mapping and can be dropped without touching portable parts;
- \`ChatRequest.tools\` is immutable tuple.

Example:

~~~python
def test_image_part_keeps_raw_bytes() -> None:
    part = ImagePart(mime_type="image/png", data=b"\x89PNG")
    self.assertEqual(part.data, b"\x89PNG")
    self.assertNotIn("base64", repr(part))
~~~

- [ ] **Step 2: Run and verify red**

~~~bash
python -m unittest tests.test_ai_contracts -v
~~~

- [ ] **Step 3: Implement immutable dataclasses**

Use typed unions for message parts. Keep the model deliberately small; do not add streaming, audio, embeddings, or vendor-specific reasoning fields.

- [ ] **Step 4: Change provider protocol**

Final protocol:

~~~python
@runtime_checkable
class AIProvider(Protocol):
    spec: TargetSpec
    health: ProviderHealth

    async def chat(self, request: ChatRequest) -> ChatResponse:
        ...

    async def aclose(self) -> None:
        ...
~~~

- [ ] **Step 5: Keep an adapter shim only inside the driver until Task 9**

Do not change router yet. If needed, add a temporary driver helper to translate the old messages/tools call into \`ChatRequest\`; remove it in Task 10.

- [ ] **Step 6: Run focused tests and commit**

~~~bash
python -m unittest tests.test_ai_contracts tests.test_provider_registry -v
git add app/ai/contracts.py app/ai/provider.py tests/test_ai_contracts.py
git commit -m "feat: define canonical AI chat contracts"
~~~

---

### Task 9: Move OpenAI serialization/deserialization fully into the driver

**Files:**
- Modify: \`app/ai/drivers/openai_chat.py\`
- Modify: \`app/ai/base.py\`
- Create: \`tests/test_openai_chat_driver.py\`
- Modify: \`tests/test_vision.py\`
- Modify: \`tests/test_ai_transport_resilience.py\`

**Interfaces:**
- Consumes: canonical \`ChatRequest\`.
- Produces: canonical \`ChatResponse\`.
- Internal helpers:
  - \`serialize_request(request: ChatRequest, model: str, supports_tools: bool) -> dict\`
  - \`parse_response(data: dict, active_tools: tuple[ToolDefinition, ...], provider_name: str) -> ChatResponse\`

- [ ] **Step 1: Write serializer tests**

Cover:
- system/user/assistant text messages;
- image part -> OpenAI \`image_url\` data URL only at serialization time;
- tool definition -> OpenAI function schema;
- tool call part -> assistant \`tool_calls\`;
- tool result part -> role \`tool\`;
- provider-local state is emitted only when present on the same-target message.

- [ ] **Step 2: Write parser tests**

Cover:
- string content;
- list text content;
- structured tool calls;
- narrow text tool-call fallback;
- malformed tool args;
- assistant replay metadata mapped into opaque provider state;
- raw response error body never leaks.

- [ ] **Step 3: Run focused tests and verify red**

~~~bash
python -m unittest tests.test_openai_chat_driver -v
~~~

- [ ] **Step 4: Implement serializer/parser**

Base64 conversion of \`ImagePart.data\` must live here. No base64 string is stored in canonical domain objects.

- [ ] **Step 5: Reduce base.py**

After the driver owns OpenAI semantics, \`base.py\` should contain only shared provider exceptions and transport failure types that are protocol-independent. Move canonical result/tool types to \`contracts.py\`.

- [ ] **Step 6: Run focused suite**

~~~bash
python -m unittest \
  tests.test_openai_chat_driver \
  tests.test_ai_transport_resilience \
  tests.test_provider_transport_retry \
  tests.test_vision -v
~~~

- [ ] **Step 7: Commit**

~~~bash
git add app/ai/drivers/openai_chat.py app/ai/base.py \
  tests/test_openai_chat_driver.py tests/test_ai_transport_resilience.py tests/test_vision.py
git commit -m "refactor: isolate OpenAI wire format in driver"
~~~

---

### Task 10: Convert router portable state to canonical messages

**Files:**
- Modify: \`app/ai/router.py\`
- Modify: \`app/ai/synthesis.py\`
- Modify: \`tests/provider_fakes.py\`
- Modify: \`tests/test_provider_portability.py\`
- Modify: \`tests/test_tool_budget_synthesis.py\`
- Modify: \`tests/test_tool_fallback_recovery.py\`

**Interfaces:**
- Consumes: canonical \`ChatRequest\`, \`ChatMessage\`, \`ToolCallPart\`, \`ToolResultPart\`.
- Produces:
  - \`AIProviderRouter.complete(request: ChatRequest, tool_executor: ToolExecutor, ...) -> CompletionResult\`

- [ ] **Step 1: Rewrite portability tests first**

The tests must express the same invariants without inspecting OpenAI dictionaries:
- provider-local state stays on the same target;
- provider-local state is removed before fallback;
- tool result evidence survives fallback;
- completed tools are not executed again;
- tool budget cannot reset across provider fallback;
- fresh synthesis contains provider-neutral evidence and no active tool definitions.

- [ ] **Step 2: Run and verify red**

~~~bash
python -m unittest tests.test_provider_portability tests.test_tool_fallback_recovery -v
~~~

- [ ] **Step 3: Replace router request state**

Use:

~~~python
@dataclass
class _RequestState:
    base_request: ChatRequest
    portable_messages: list[ChatMessage]
    tool_outputs: list[str]
    ...
~~~

Do not deepcopy raw vendor dictionaries because they no longer exist.

- [ ] **Step 4: Replace assistant/tool dictionary builders**

Delete:
- \`_tool_call_message\`
- \`_assistant_tool_message\`
- OpenAI-specific \`_json_dumps\` usage in router

Replace with canonical \`ChatMessage\` parts.

Same-target messages retain \`provider_state\`; portable messages copy the assistant/tool semantic parts without provider state.

- [ ] **Step 5: Convert fresh synthesis**

\`build_fresh_synthesis_messages\` must return canonical \`ChatMessage\` objects and must not know OpenAI roles/tool-call wire syntax.

- [ ] **Step 6: Run router/tool suites**

~~~bash
python -m unittest \
  tests.test_provider_portability \
  tests.test_tool_budget_synthesis \
  tests.test_tool_fallback_recovery \
  tests.test_provider_routing \
  tests.test_provider_target_rotation -v
~~~

Expected: PASS.

- [ ] **Step 7: Commit**

~~~bash
git add app/ai/router.py app/ai/synthesis.py tests/provider_fakes.py \
  tests/test_provider_portability.py tests/test_tool_budget_synthesis.py \
  tests/test_tool_fallback_recovery.py
git commit -m "refactor: make router state provider-neutral"
~~~

---

### Task 11: Convert Orchestrator and multimodal input to canonical requests

**Files:**
- Modify: \`app/ai/multimodal.py\`
- Modify: \`app/core/orchestrator.py\`
- Modify: \`tests/test_vision.py\`
- Modify: \`tests/test_synthesis_context.py\`
- Modify: \`tests/test_search_policy_prompt.py\`
- Modify: \`tests/test_url_tool_integration.py\`

**Interfaces:**
- Consumes: \`UserRequest\`.
- Produces:
  - \`build_user_parts(request: UserRequest) -> tuple[MessagePart, ...]\`
  - Orchestrator builds \`ChatRequest(messages=..., tools=...)\`.

- [ ] **Step 1: Rewrite multimodal tests**

Replace assertions over OpenAI \`{"type": "image_url"}\` dictionaries with canonical parts:

~~~python
content = build_user_parts(request)
self.assertEqual(
    [type(part) for part in content],
    [TextPart, ImagePart, TextPart, ImagePart],
)
self.assertEqual(content[1].data, b"reply")
self.assertEqual(content[3].data, b"current")
~~~

- [ ] **Step 2: Run vision/orchestrator tests and verify red**

~~~bash
python -m unittest tests.test_vision tests.test_synthesis_context -v
~~~

- [ ] **Step 3: Replace build_user_content**

Rename to \`build_user_parts\`. It must:
- keep quoted text before reply images;
- keep user question before current images;
- preserve current truncation limits;
- never base64 encode.

- [ ] **Step 4: Convert history/system/user messages**

Orchestrator must construct canonical \`ChatMessage\` values for:
- system prompt;
- history;
- final user message.

- [ ] **Step 5: Convert TOOLS**

Replace OpenAI-shaped tool dictionaries with canonical \`ToolDefinition\` values. Keep names, descriptions, JSON schema, and behavior unchanged.

- [ ] **Step 6: Change router invocation**

Use:

~~~python
result = await self.router.complete(
    ChatRequest(messages=tuple(messages), tools=TOOLS),
    tool_executor,
    requires_vision=request.requires_vision,
    image_count=len(request.images),
    operations=operations,
)
~~~

- [ ] **Step 7: Run orchestrator/search/tool regressions**

~~~bash
python -m unittest \
  tests.test_vision \
  tests.test_synthesis_context \
  tests.test_search_policy_prompt \
  tests.test_url_tool_integration \
  tests.test_fresh_synthesis_routing -v
~~~

Expected: PASS.

- [ ] **Step 8: Commit**

~~~bash
git add app/ai/multimodal.py app/core/orchestrator.py \
  tests/test_vision.py tests/test_synthesis_context.py \
  tests/test_search_policy_prompt.py tests/test_url_tool_integration.py
git commit -m "refactor: build canonical AI requests in orchestrator"
~~~

---

### Task 12: Remove compatibility shims and prove protocol independence

**Files:**
- Modify: \`app/ai/provider.py\`
- Modify: \`app/ai/router.py\`
- Modify: \`app/ai/drivers/openai_chat.py\`
- Modify: \`app/ai/base.py\`
- Modify: \`tests/test_provider_portability.py\`
- Create: \`tests/test_ai_architecture_boundaries.py\`
- Modify: \`README.md\`

**Interfaces:**
- Final public runtime seam:
  - \`AIProvider.chat(request: ChatRequest) -> ChatResponse\`
  - \`AIProvider.spec: TargetSpec\`
  - \`AIProviderRouter.complete(request: ChatRequest, tool_executor, ...) -> CompletionResult\`

- [ ] **Step 1: Add architecture-boundary source tests**

The test reads runtime source and asserts:
- \`app/ai/router.py\` contains no \`"tool_calls"\`, \`"image_url"\`, \`"chat/completions"\`, \`"reasoning_content"\`;
- \`app/core/orchestrator.py\` contains no OpenAI wire \`tool_calls\` construction;
- \`app/ai/recovery.py\` contains no provider family names;
- \`app/config.py\` contains no \`chainnode_api_keys\`, \`xkiro_api_keys\`, provider-specific model fields;
- OpenAI wire vocabulary is concentrated in \`app/ai/drivers/openai_chat.py\`.

- [ ] **Step 2: Run and verify red**

~~~bash
python -m unittest tests.test_ai_architecture_boundaries -v
~~~

- [ ] **Step 3: Delete temporary aliases/shims**

Remove:
- old \`chat(messages, tools)\` compatibility;
- old OpenAI-shaped multimodal builder;
- dynamic target metadata compatibility;
- legacy provider env aliases;
- redundant registry/factory tests.

- [ ] **Step 4: Add driver-independent fake**

Create a fake target in tests whose \`chat(ChatRequest)\` implementation has no OpenAI semantics. Route it through tools and fallback to prove router behavior does not depend on the OpenAI driver.

- [ ] **Step 5: Document provider onboarding**

README must include two recipes:

**Add OpenAI-compatible provider**
1. add provider profile to \`config/ai-providers.toml\`;
2. add \`AI_PROVIDERS__<ID>__*\` values;
3. add provider id to route order;
4. no Python runtime change.

**Add new native protocol**
1. implement one driver under \`app/ai/drivers/\`;
2. register driver id in \`drivers/registry.py\`;
3. add provider profile using that driver;
4. no router/retry/health/orchestrator change.

- [ ] **Step 6: Run complete offline verification**

~~~bash
python -m ruff check .
python -m pip check
python -m compileall -q app tests scripts
python scripts/check_canonical_env_vocabulary.py
python scripts/check_markdown_links.py
python tests/run_tests.py
python -m unittest discover -s tests -p 'test_*.py' -v
~~~

Expected: PASS.

- [ ] **Step 7: Validate Compose configuration**

~~~bash
cp .env.example .env
docker compose config --quiet
rm .env
~~~

Expected: exit 0.

- [ ] **Step 8: Build production image**

~~~bash
docker build --tag fcai-generic-provider:local .
~~~

Expected: exit 0.

- [ ] **Step 9: Commit**

~~~bash
git add -A
git commit -m "refactor: complete generic AI provider architecture"
~~~

---

## Final Review Checklist

Before opening a PR:

- [ ] \`git grep -nE 'identity\\.family ==|family == "chainnode"|family == "xkiro" app/ai\` returns no provider-specific recovery branches.
- [ ] \`git grep -nE 'build_chainnode_provider_slots|build_xkiro_provider_slots|PROVIDER_FACTORIES' app tests\` returns no runtime construction references.
- [ ] \`git grep -nE 'CHAINNODE_API_KEYS|XKIRO_API_KEYS|CHAINNODE_TEXT_MODELS|XKIRO_TEXT_MODELS' app\` returns no provider-specific Settings/runtime references.
- [ ] A config-only fixture OpenAI-compatible provider builds, routes, handles tools, and participates in fallback without any new Python provider module.
- [ ] No raw secret appears in target repr, exception text, logs, status lines, or test diagnostics.
- [ ] Existing Chainnode primary -> xKiro fallback behavior is unchanged.
- [ ] Existing one-image route behavior is unchanged.
- [ ] Existing tool-call continuation and cross-provider portability behavior is unchanged.
- [ ] Existing target/model/credential rotation counters remain unchanged.
- [ ] Full CI-equivalent offline verification passes on Python 3.11 and 3.12 in CI.

## Suggested PR Decomposition

If review size becomes too large, keep one branch lineage but open stacked PRs at these stable checkpoints:

1. **PR A — Generic construction:** Tasks 1-7. Delivers config-only OpenAI-compatible provider onboarding.
2. **PR B — Canonical chat model:** Tasks 8-12. Delivers protocol-independent router/orchestrator seams.

PR A must be independently deployable and behavior-preserving. PR B must not begin by rewriting retry/recovery logic; it only changes message/request representation after PR A is stable.
