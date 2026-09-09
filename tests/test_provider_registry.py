"""Generic provider contract, registry, and ordering regressions."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app.ai.base import ChatResponse
from app.ai.bai import build_bai_provider_slots
from app.ai.capabilities import ProviderCapabilities
from app.ai.health import ProviderHealth
from app.ai.provider import AIProvider
from app.ai.registry import build_registered_providers
from app.ai.router import AIProviderRouter, build_provider_router
from app.config import Settings


class FakeProvider:
    def __init__(self, name: str, route: str = "text", max_images: int = 0) -> None:
        self.name = name
        self.model = "fake-model"
        self.supports_tools = True
        self.capabilities = ProviderCapabilities(
            route=route,
            supports_vision=route == "vision",
            max_images=max_images if route == "vision" else 0,
        )
        self.health = ProviderHealth()

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> ChatResponse:
        return ChatResponse(content="ok")

    async def aclose(self) -> None:
        return None


async def _close_slots(slots: list[AIProvider]) -> None:
    for provider in slots:
        await provider.aclose()


async def _close_router(router: AIProviderRouter) -> None:
    for provider in router.providers:
        await provider.aclose()


class ProviderContractTests(unittest.TestCase):
    def test_non_openai_fake_satisfies_provider_protocol(self) -> None:
        self.assertIsInstance(FakeProvider("fake"), AIProvider)

    def test_unknown_name_is_rejected_before_factory_runs(self) -> None:
        calls = 0

        def build_fake(_settings: Settings) -> list[AIProvider]:
            nonlocal calls
            calls += 1
            return [FakeProvider("fake")]

        with patch.dict(
            "app.ai.registry.PROVIDER_FACTORIES",
            {"fake": build_fake},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "chưa được đăng ký"):
                build_registered_providers(
                    Settings(_env_file=None),
                    ["fake", "missing"],
                )
        self.assertEqual(calls, 0)

    def test_bai_family_factory_returns_text_and_vision_slots_under_same_key(self) -> None:
        slots = build_bai_provider_slots(
            Settings(_env_file=None, bai_api_key="secret")
        )
        try:
            self.assertEqual([slot.name for slot in slots], ["bai", "bai"])
            self.assertEqual(
                [slot.capabilities.route for slot in slots],
                ["text", "vision"],
            )
            self.assertEqual(slots[1].capabilities.max_images, 1)
        finally:
            asyncio.run(_close_slots(slots))


class ProviderOrderTests(unittest.TestCase):
    def test_provider_orders_default_to_bai(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.text_provider_order_list, ["bai"])
        self.assertEqual(settings.vision_provider_order_list, ["bai"])

    def test_orders_normalize_and_preserve_first_occurrence(self) -> None:
        settings = Settings(
            _env_file=None,
            text_provider_order=" Foo,bai,foo,BAR ",
            vision_provider_order="bar,BAI,bar",
        )
        self.assertEqual(settings.text_provider_order_list, ["foo", "bai", "bar"])
        self.assertEqual(settings.vision_provider_order_list, ["bar", "bai"])

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

    def test_production_router_is_bai_only_but_order_aware(self) -> None:
        router = build_provider_router(
            Settings(_env_file=None, bai_api_key="secret")
        )
        try:
            self.assertEqual(router.provider_order(False), ("bai",))
            self.assertEqual(router.provider_order(True), ("bai",))
            self.assertEqual(router.configured_provider_names(False), ("bai",))
            self.assertEqual(router.configured_provider_names(True), ("bai",))
            self.assertEqual(router.max_supported_images(), 1)
        finally:
            asyncio.run(_close_router(router))

    def test_unknown_order_name_fails_during_build(self) -> None:
        settings = Settings(
            _env_file=None,
            text_provider_order="missing",
            vision_provider_order="",
        )
        with self.assertRaisesRegex(ValueError, "chưa được đăng ký"):
            build_provider_router(settings)


if __name__ == "__main__":
    unittest.main()
