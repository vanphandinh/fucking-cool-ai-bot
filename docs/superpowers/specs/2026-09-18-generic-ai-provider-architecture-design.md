# Generic AI Provider Architecture Design

Date: 2026-09-18

## Context

The current AI runtime already has a strong generic routing core: capability filtering, provider ordering, scoped health, bounded transport retry, target rotation, provider fallback, tool execution, portable tool state, and fresh synthesis. The main coupling that prevents low-cost provider onboarding is outside that routing loop: provider-specific Settings fields, duplicated Chainnode/xKiro factories, a provider-family registry, vendor-name checks in recovery policy, and OpenAI-shaped message dictionaries crossing the router seam.

This design keeps the proven routing/recovery behavior and moves variability behind two smaller seams:

1. ProviderProfile: declarative provider-family data.
2. Driver: protocol-specific code that knows how to serialize requests, call an endpoint, parse responses, and normalize failures.

A concrete model/credential/route combination is represented by an immutable TargetSpec. The router only knows TargetSpec plus the ChatTarget interface.

## Goals

- Adding an OpenAI-compatible provider requires no Python source changes.
- Adding a provider that uses a new native protocol requires one new driver plus configuration, without changing router, retry, health, or orchestration code.
- Preserve deterministic model-major x credential target ordering.
- Preserve text and vision provider ordering.
- Preserve all current retry, recovery-hop, health-scope, tool-budget, portable-state, and provider-metadata isolation semantics.
- Provider secrets must never enter target identities, repr output, logs, status output, or exceptions.
- Keep Python 3.11 and 3.12 support and avoid new runtime dependencies where the standard library is sufficient.
- Keep the current 64 enabled-target safety bound.

## Non-goals

- Do not replace the existing router with LiteLLM, LangChain, Pydantic AI, or another orchestration framework.
- Do not add dynamic third-party Python plugin loading in the first implementation. A small in-repo driver registry is sufficient until there is a real external driver package.
- Do not change end-user Telegram behavior.
- Do not change retry budgets or provider ordering defaults as part of the refactor.
- Do not query provider model catalogs on every startup or request.

## Target architecture

Orchestrator
  -> canonical ChatRequest / ToolDefinition
  -> AIProviderRouter
  -> ChatTarget interface
  -> TargetSpec
  -> protocol Driver
  -> provider endpoint

Provider family configuration is split into:

- catalog data: provider id, driver id, route capabilities, default endpoint/timeout, recovery overrides, driver options;
- runtime environment data: credentials, endpoint override, text models, vision models, timeout override;
- route ordering: TEXT_PROVIDER_ORDER and VISION_PROVIDER_ORDER.

The router continues to own retries, health, fallback, tool execution, and portable conversation state. Drivers perform exactly one network attempt per chat call.

## Provider runtime configuration

Settings gains a single dynamic mapping:

~~~python
class ProviderRuntimeSettings(BaseModel):
    api_keys: str = ""
    base_url: str = ""
    text_models: str = ""
    vision_models: str = ""
    request_timeout_sec: float | None = None

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        hide_input_in_errors=True,
    )

    ai_providers: dict[str, ProviderRuntimeSettings] = {}
~~~

Environment keys use a generic namespace:

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

This keeps secrets in environment configuration while removing provider-specific Python fields. Adding another provider only adds configuration values and a catalog entry.

## Provider catalog

config/ai-providers.toml is the declarative catalog. Each provider declares its driver and route/recovery behavior.

Example shape:

~~~toml
[[providers]]
id = "chainnode"
driver = "openai-chat"
default_base_url = "https://dn.chainno.de/v1"
default_timeout_sec = 60.0

[providers.routes.text]
enabled = true

[providers.routes.vision]
enabled = true
supports_vision = true
max_images = 1

[[providers]]
id = "xkiro"
driver = "openai-chat"
default_base_url = "https://api.xkiro.com/v1"
default_timeout_sec = 60.0

[providers.routes.text]
enabled = true

[providers.routes.vision]
enabled = true
supports_vision = true
max_images = 1

[[providers.recovery]]
status = 429
action = "rotate_target"
scope = "credential"
effect = "cooldown"

[[providers.recovery]]
status = 402
action = "rotate_target"
scope = "credential"
effect = "disable"

[[providers.recovery]]
status = 403
action = "rotate_target"
scope = "entitlement"
effect = "disable"
~~~

Catalog loading validates duplicate ids, unknown routes, invalid recovery enum values, invalid image limits, and unknown drivers before any network client is created.

## TargetSpec

Target identity becomes explicit rather than inferred through getattr.

~~~python
@dataclass(frozen=True, slots=True)
class TargetSpec:
    identity: ProviderTargetIdentity
    driver: str
    capabilities: ProviderCapabilities
    base_url: str
    request_timeout_sec: float
    recovery_policy: RecoveryPolicy
    driver_options: Mapping[str, object]
~~~

Raw credentials are deliberately absent from TargetSpec. The target builder passes a credential only to the driver constructor. Identity continues to use opaque aliases such as cred-1 and target ids such as chainnode:text:m1:c1.

## Driver seam

The external router seam remains small:

~~~python
@runtime_checkable
class ChatTarget(Protocol):
    spec: TargetSpec
    health: ProviderHealth

    async def chat(self, request: ChatRequest) -> ChatResponse:
        ...

    async def aclose(self) -> None:
        ...
~~~

During Milestone A, chat may temporarily retain the existing messages/tools parameters to minimize migration risk. Milestone B changes that interface to ChatRequest after target construction and recovery are already generic.

The first driver is openai-chat. It owns HTTPX setup, OpenAI-compatible payload serialization, structured tool-call parsing, narrow text-tool-call fallback parsing, assistant replay metadata, and HTTP error normalization.

The driver registry is keyed by protocol, not vendor:

~~~python
DRIVERS = {
    "openai-chat": OpenAIChatDriver(),
}
~~~

This registry changes only when a new wire protocol is added.

## Generic target builder

The target builder:

1. reads provider order;
2. loads the ProviderProfile;
3. reads the matching dynamic runtime settings;
4. resolves base URL and timeout overrides;
5. parses credential/model pools preserving case and first occurrence;
6. expands each enabled route in deterministic model-major x credential order;
7. creates a secret-free TargetSpec;
8. asks the selected driver to build a target using TargetSpec plus the raw credential;
9. enforces the global 64-target maximum.

Provider-family factory modules are no longer required.

## Recovery policy

Generic HTTP recovery defaults remain in code because they describe protocol-level behavior shared by most providers:

- transport failure: record-only target decision; transport retry logic remains separate;
- 401: rotate target, disable credential;
- 404: rotate target, disable model;
- 429: rotate target, cool down model;
- 403: fallback family, disable family;
- 5xx: fallback family, transient family health;
- otherwise: fallback family, transient or record-only based on ProviderError.transient.

Provider profiles may override exact status codes. xKiro uses overrides for 402, 403, and 429. classify_recovery must not compare provider-family strings.

## Canonical chat model

Milestone B removes OpenAI message semantics from the router/orchestrator seam.

Canonical types cover:

- ChatMessage(role, parts, provider_state);
- TextPart;
- ImagePart(mime_type, bytes);
- ToolCall;
- ToolResultPart;
- ToolDefinition(name, description, JSON schema);
- ChatRequest(messages, tools);
- ChatResponse(content, tool_calls, provider_state).

Provider state is opaque and target-local. It is retained for same-target continuation and removed when building portable fallback state. This preserves the current reasoning_details/reasoning/reasoning_content behavior without making those OpenAI fields part of the domain model.

Image base64 encoding moves from app/ai/multimodal.py into the OpenAI driver serializer. Domain request objects continue to hold raw bytes and must not include base64 in repr.

## Migration strategy

Milestone A:
- add generic settings and catalog while keeping legacy fields temporarily;
- introduce TargetSpec;
- introduce the protocol driver seam;
- add the generic target builder;
- switch build_provider_router to the generic builder;
- make recovery policy declarative;
- migrate environment vocabulary;
- remove chainnode.py, xkiro.py, and the vendor registry;
- prove a third OpenAI-compatible provider can be added through config-only fixtures.

Milestone B:
- introduce canonical chat/tool/message types;
- move OpenAI wire formatting fully into the openai-chat driver;
- convert router portable state to canonical messages;
- convert Orchestrator and multimodal construction;
- remove temporary compatibility aliases and OpenAI-shaped interfaces.

Each milestone must preserve the existing full unittest suite before cleanup proceeds.

## Acceptance criteria

Milestone A is accepted when:
- Chainnode and xKiro routing behavior is unchanged;
- current retry/recovery/tool portability tests pass;
- no app/ai runtime module contains family-specific construction branches;
- recovery.py contains no chainnode/xkiro family-name checks;
- a fixture provider using driver=openai-chat builds and routes without editing Python source;
- provider secrets remain absent from target identity, logs, exceptions, status, and repr;
- full CI-equivalent offline checks pass.

Milestone B is accepted when:
- router and orchestrator no longer create or inspect OpenAI tool_call/message wire dictionaries;
- OpenAI serialization/parsing lives under app/ai/drivers/openai_chat.py;
- provider-local replay metadata remains local to the originating target;
- tool outputs remain portable across target/provider fallback;
- vision bytes remain canonical until the driver serialization step;
- full CI-equivalent offline checks pass.

## Deletion test

After migration, deleting ProviderProfile/catalog/target-builder logic would force provider-specific model/credential expansion, validation, recovery configuration, and endpoint setup back into multiple provider modules. The module therefore earns its seam.

Deleting the openai-chat driver would force OpenAI wire-format knowledge into the router/orchestrator. The driver therefore earns its seam.
