"""Generic provider contract and ordering regressions."""

from __future__ import annotations

import asyncio
import importlib.util
import unittest

from app.ai.contracts import ChatMessage, ChatRequest, ChatResponse, TextPart
from app.ai.capabilities import ProviderCapabilities
from app.ai.health import ProviderHealth
from app.ai.provider import AIProvider
from app.ai.recovery import RecoveryPolicy
from app.ai.router import AIProviderRouter, build_provider_router
from app.ai.target import ProviderTargetIdentity, TargetSpec
from app.ai.target_builder import build_provider_targets
from app.config import Settings


class FakeProvider:
    def __init__(self, name: str, route: str = "text", max_images: int = 0) -> None:
        capabilities = ProviderCapabilities(
            route=route,
            supports_vision=route == "vision",
            max_images=max_images if route == "vision" else 0,
        )
        self.spec = TargetSpec(
            identity=ProviderTargetIdentity(
                family=name,
                route=route,
                model="fake-model",
                credential_id="cred-1",
                target_id=f"{name}:{route}:fake-model:cred-1",
            ),
            driver="fake",
            capabilities=capabilities,
            base_url="https://example.invalid/v1",
            request_timeout_sec=60.0,
            recovery_policy=RecoveryPolicy(),
        )
        self.supports_tools = True
        self.health = ProviderHealth()

    @property
    def name(self) -> str:
        return self.spec.identity.family

    @property
    def model(self) -> str:
        return self.spec.identity.model

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self.spec.capabilities

    async def chat(self, request: ChatRequest) -> ChatResponse:
        del request
        return ChatResponse(content="ok")

    async def aclose(self) -> None:
        return None


class MinimalProtocolProvider:
    """Target implementing only the public AIProvider protocol surface."""

    def __init__(self) -> None:
        self.spec = TargetSpec(
            identity=ProviderTargetIdentity(
                family="minimal",
                route="text",
                model="minimal-model",
                credential_id="cred-1",
                target_id="minimal:text:m1:c1",
            ),
            driver="minimal",
            capabilities=ProviderCapabilities(route="text"),
            base_url="https://example.invalid/v1",
            request_timeout_sec=60.0,
            recovery_policy=RecoveryPolicy(),
        )
        self.health = ProviderHealth()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        del request
        return ChatResponse(content="minimal-ok")

    async def aclose(self) -> None:
        return None


async def _close_slots(slots: list[AIProvider]) -> None:
    for provider in slots:
        await provider.aclose()


async def _close_router(router: AIProviderRouter) -> None:
    for provider in router.providers:
        await provider.aclose()


class ProviderContractTests(unittest.TestCase):
    def test_provider_family_runtime_modules_are_removed(self) -> None:
        self.assertIsNone(importlib.util.find_spec("app.ai.chainnode"))
        self.assertIsNone(importlib.util.find_spec("app.ai.xkiro"))
        self.assertIsNone(importlib.util.find_spec("app.ai.registry"))

    def test_non_openai_fake_satisfies_provider_protocol(self) -> None:
        self.assertIsInstance(FakeProvider("fake"), AIProvider)


    def test_router_uses_only_public_provider_protocol(self) -> None:
        provider = MinimalProtocolProvider()
        self.assertIsInstance(provider, AIProvider)
        router = AIProviderRouter([provider])

        async def noop_tool(_name: str, _args: dict) -> str:
            return "unused"

        result = asyncio.run(
            router.complete(
                ChatRequest(
                    messages=(ChatMessage("user", (TextPart("hello"),)),),
                ),
                noop_tool,
            )
        )
        self.assertEqual((result.content, result.provider), ("minimal-ok", "minimal"))

    def test_unknown_name_is_rejected_before_target_construction(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown AI provider"):
            build_provider_targets(Settings(_env_file=None), ["missing"])

    def test_xkiro_generic_builder_returns_text_and_vision_targets(self) -> None:
        targets = build_provider_targets(
            Settings(
                _env_file=None,
                ai_providers={
                    "xkiro": {
                        "api_keys": "test-key",
                        "text_models": "test-text-model",
                        "vision_models": "test-vision-model",
                    }
                },
                text_provider_order="xkiro",
                vision_provider_order="xkiro",
            ),
            ["xkiro"],
        )
        try:
            self.assertEqual([target.name for target in targets], ["xkiro", "xkiro"])
            self.assertEqual(
                [target.capabilities.route for target in targets],
                ["text", "vision"],
            )
        finally:
            asyncio.run(_close_slots(targets))


class ProviderOrderTests(unittest.TestCase):
    def test_provider_orders_default_to_chainnode_then_xkiro(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.text_provider_order_list, ["chainnode", "xkiro"])
        self.assertEqual(settings.vision_provider_order_list, ["chainnode", "xkiro"])

    def test_orders_reject_unknown_catalog_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown AI provider"):
            Settings(
                _env_file=None,
                text_provider_order="foo,xkiro",
                vision_provider_order="xkiro",
            )

    def test_text_and_vision_orders_route_independently(self) -> None:
        providers: list[AIProvider] = [
            FakeProvider("first", "text"),
            FakeProvider("first", "vision", 2),
            FakeProvider("second", "text"),
            FakeProvider("second", "vision", 4),
        ]
        router = AIProviderRouter(
            providers,
            text_provider_order=("first", "second"),
            vision_provider_order=("second", "first"),
        )
        text = router.capable_providers(requires_vision=False)
        vision = router.capable_providers(requires_vision=True, image_count=1)
        self.assertEqual([provider.name for provider in text], ["first", "second"])
        self.assertEqual([provider.name for provider in vision], ["second", "first"])

    def test_production_router_keeps_chainnode_primary_and_xkiro_fallback(self) -> None:
        router = build_provider_router(
            Settings(
                _env_file=None,
                ai_providers={
                    "chainnode": {
                        "api_keys": "test-chainnode-key",
                        "text_models": "test-chainnode-text",
                        "vision_models": "test-chainnode-vision",
                    },
                    "xkiro": {
                        "api_keys": "test-xkiro-key",
                        "text_models": "test-xkiro-text",
                        "vision_models": "test-xkiro-vision",
                    },
                },
            )
        )
        try:
            self.assertEqual(router.provider_order(False), ("chainnode", "xkiro"))
            self.assertEqual(router.provider_order(True), ("chainnode", "xkiro"))
            self.assertEqual(
                router.configured_provider_names(False),
                ("chainnode", "xkiro"),
            )
            self.assertEqual(
                router.configured_provider_names(True),
                ("chainnode", "xkiro"),
            )
            self.assertEqual(router.max_supported_images(), 1)
        finally:
            asyncio.run(_close_router(router))


if __name__ == "__main__":
    unittest.main()
