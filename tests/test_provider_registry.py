"""Generic provider contract and registry regressions."""

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


if __name__ == "__main__":
    unittest.main()
