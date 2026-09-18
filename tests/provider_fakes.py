"""Reusable protocol-independent provider fakes for router regressions."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from app.ai.capabilities import ProviderCapabilities
from app.ai.catalog import load_provider_catalog
from app.ai.contracts import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    TextPart,
    ToolCallPart,
    ToolDefinition,
)
from app.ai.health import ProviderHealth
from app.ai.recovery import RecoveryDecision, RecoveryPolicy
from app.ai.target import ProviderTargetIdentity, TargetSpec


def _policy_for_family(name: str) -> RecoveryPolicy:
    try:
        profile = load_provider_catalog().require(name)
    except ValueError:
        return RecoveryPolicy()
    overrides = {
        rule.status: RecoveryDecision(rule.action, rule.scope, rule.effect)
        for rule in profile.recovery_rules
    }
    return RecoveryPolicy(MappingProxyType(overrides))


class ScriptedProvider:
    def __init__(
        self,
        name: str,
        script: list[ChatResponse | Exception],
        *,
        route: str = "text",
        max_images: int = 0,
        target_id: str | None = None,
        credential: str = "unused-test-credential",
        credential_id: str = "cred-1",
        model: str | None = None,
    ) -> None:
        del credential  # Raw test credentials are deliberately never retained.
        configured_model = model or f"{name}-model"
        capabilities = ProviderCapabilities(
            route=route,
            supports_vision=route == "vision",
            max_images=max_images if route == "vision" else 0,
        )
        self.spec = TargetSpec(
            identity=ProviderTargetIdentity(
                family=name,
                route=route,
                model=configured_model,
                credential_id=credential_id,
                target_id=target_id
                or f"{name}:{route}:{configured_model}:{credential_id}",
            ),
            driver="fake",
            capabilities=capabilities,
            base_url="https://example.invalid/v1",
            request_timeout_sec=60.0,
            recovery_policy=_policy_for_family(name),
        )
        self.health = ProviderHealth()
        self.script = list(script)
        self.requests: list[ChatRequest] = []

    @property
    def name(self) -> str:
        return self.spec.identity.family

    @property
    def model(self) -> str:
        return self.spec.identity.model

    @model.setter
    def model(self, value: str) -> None:
        identity = replace(self.spec.identity, model=value)
        self.spec = replace(self.spec, identity=identity)

    @property
    def credential_id(self) -> str:
        return self.spec.identity.credential_id

    @credential_id.setter
    def credential_id(self, value: str) -> None:
        identity = replace(self.spec.identity, credential_id=value)
        self.spec = replace(self.spec, identity=identity)

    @property
    def target_id(self) -> str:
        return self.spec.identity.target_id

    @target_id.setter
    def target_id(self, value: str) -> None:
        identity = replace(self.spec.identity, target_id=value)
        self.spec = replace(self.spec, identity=identity)

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self.spec.capabilities

    @property
    def supports_tools(self) -> bool:
        return self.spec.capabilities.supports_tools

    @supports_tools.setter
    def supports_tools(self, value: bool) -> None:
        capabilities = replace(self.spec.capabilities, supports_tools=value)
        self.spec = replace(self.spec, capabilities=capabilities)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:
        return None


def text_request(
    text: str = "hello",
    *,
    tools: tuple[ToolDefinition, ...] = (),
) -> ChatRequest:
    return ChatRequest(
        messages=(ChatMessage("user", (TextPart(text),)),),
        tools=tools,
    )


def fetch_url_definition() -> ToolDefinition:
    return ToolDefinition(
        name="fetch_url",
        description="Fetch URL",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    )


async def noop_tool(_name: str, _args: dict) -> str:
    return "unused"


def tool_response(count: int, prefix: str = "call") -> ChatResponse:
    return ChatResponse(
        tool_calls=tuple(
            ToolCallPart(
                id=f"{prefix}_{index}",
                name="fetch_url",
                arguments={"url": f"https://example.com/{prefix}-{index}"},
            )
            for index in range(count)
        )
    )
