"""Concrete provider-pool factory ordering and capability regressions."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.chainnode import build_chainnode_provider_slots
from app.ai.xkiro import build_xkiro_provider_slots
from app.config import Settings


class ProviderPoolFactoryTests(unittest.TestCase):
    def _close(self, slots) -> None:
        for slot in slots:
            asyncio.run(slot.aclose())

    def test_chainnode_is_model_major_then_credential_for_each_route(self) -> None:
        settings = Settings(
            _env_file=None,
            chainnode_api_keys="ChainKeyOne,ChainKeyTwo",
            chainnode_text_models="DeepSeek/Case,GLM/Case",
            chainnode_vision_models="Vision/Case",
            text_provider_order="chainnode",
            vision_provider_order="chainnode",
        )
        slots = build_chainnode_provider_slots(settings)
        try:
            self.assertEqual(
                [
                    (
                        slot.capabilities.route,
                        slot.model,
                        slot.credential_id,
                        slot.target_id,
                    )
                    for slot in slots
                ],
                [
                    ("text", "DeepSeek/Case", "cred-1", "chainnode:text:m1:c1"),
                    ("text", "DeepSeek/Case", "cred-2", "chainnode:text:m1:c2"),
                    ("text", "GLM/Case", "cred-1", "chainnode:text:m2:c1"),
                    ("text", "GLM/Case", "cred-2", "chainnode:text:m2:c2"),
                    ("vision", "Vision/Case", "cred-1", "chainnode:vision:m1:c1"),
                    ("vision", "Vision/Case", "cred-2", "chainnode:vision:m1:c2"),
                ],
            )
            self.assertTrue(all(not slot.capabilities.supports_vision for slot in slots[:4]))
            self.assertTrue(all(slot.capabilities.supports_vision for slot in slots[4:]))
        finally:
            self._close(slots)

    def test_xkiro_is_model_major_then_credential_for_each_route(self) -> None:
        settings = Settings(
            _env_file=None,
            xkiro_api_keys="KeyOne,KeyTwo",
            xkiro_text_models="ModelA,ModelB",
            xkiro_vision_models="VisionA",
            text_provider_order="xkiro",
            vision_provider_order="xkiro",
        )
        slots = build_xkiro_provider_slots(settings)
        try:
            self.assertEqual(
                [
                    (
                        slot.capabilities.route,
                        slot.model,
                        slot.credential_id,
                        slot.target_id,
                    )
                    for slot in slots
                ],
                [
                    ("text", "ModelA", "cred-1", "xkiro:text:m1:c1"),
                    ("text", "ModelA", "cred-2", "xkiro:text:m1:c2"),
                    ("text", "ModelB", "cred-1", "xkiro:text:m2:c1"),
                    ("text", "ModelB", "cred-2", "xkiro:text:m2:c2"),
                    ("vision", "VisionA", "cred-1", "xkiro:vision:m1:c1"),
                    ("vision", "VisionA", "cred-2", "xkiro:vision:m1:c2"),
                ],
            )
            self.assertTrue(all(not slot.capabilities.supports_vision for slot in slots[:4]))
            self.assertTrue(all(slot.capabilities.supports_vision for slot in slots[4:]))
        finally:
            self._close(slots)


if __name__ == "__main__":
    unittest.main()
