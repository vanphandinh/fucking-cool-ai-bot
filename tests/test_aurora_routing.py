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


if __name__ == "__main__":
    unittest.main()
