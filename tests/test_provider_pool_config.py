"""Backward-compatible plural provider-pool configuration contracts."""

from __future__ import annotations

import asyncio
import unittest

from pydantic import ValidationError

from app.ai.xkiro import build_xkiro_provider_slots
from app.config import Settings


class ProviderPoolConfigTests(unittest.TestCase):
    def test_legacy_scalar_becomes_one_element_pool(self) -> None:
        settings = Settings(
            _env_file=None,
            chainnode_text_model="Legacy/ModelCase",
            xkiro_api_key="LegacyKeyCase",
            xkiro_text_model="LegacyX/Model",
            vision_enabled=False,
        )

        self.assertEqual(settings.chainnode_text_models_list, ["Legacy/ModelCase"])
        self.assertEqual(settings.xkiro_api_keys_list, ["LegacyKeyCase"])
        self.assertEqual(settings.xkiro_text_models_list, ["LegacyX/Model"])

    def test_explicit_plural_wins_and_preserves_case(self) -> None:
        settings = Settings(
            _env_file=None,
            chainnode_api_key="key",
            chainnode_text_model="legacy",
            chainnode_text_models="Model/A,model/B,Model/A",
            xkiro_api_key="legacy-key",
            xkiro_api_keys="KeyOne,keyTWO",
            xkiro_text_model="legacy-model",
            xkiro_text_models="X/One,x/Two",
            vision_enabled=False,
        )

        self.assertEqual(settings.chainnode_text_models_list, ["Model/A", "model/B"])
        self.assertEqual(settings.xkiro_api_keys_list, ["KeyOne", "keyTWO"])
        self.assertEqual(settings.xkiro_text_models_list, ["X/One", "x/Two"])

    def test_explicit_blank_plural_disables_scalar_fallback(self) -> None:
        settings = Settings(
            _env_file=None,
            chainnode_text_model="legacy",
            chainnode_text_models="",
            xkiro_api_key="legacy-key",
            xkiro_api_keys="",
            xkiro_text_model="legacy-model",
            xkiro_text_models="",
            text_provider_order="",
            vision_provider_order="",
        )

        self.assertEqual(settings.chainnode_text_models_list, [])
        self.assertEqual(settings.xkiro_api_keys_list, [])
        self.assertEqual(settings.xkiro_text_models_list, [])

    def test_recovery_hop_limit_is_bounded(self) -> None:
        self.assertEqual(
            Settings(_env_file=None).provider_recovery_max_hops_per_request,
            5,
        )
        for invalid in (0, 21):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    Settings(
                        _env_file=None,
                        provider_recovery_max_hops_per_request=invalid,
                    )

    def test_xkiro_factory_is_model_major_then_credential(self) -> None:
        settings = Settings(
            _env_file=None,
            xkiro_api_keys="KeyOne,KeyTwo",
            xkiro_text_models="ModelA,ModelB",
            text_provider_order="xkiro",
            vision_enabled=False,
        )
        slots = build_xkiro_provider_slots(settings)
        try:
            self.assertEqual(
                [(slot.model, slot.credential_id, slot.target_id) for slot in slots],
                [
                    ("ModelA", "cred-1", "xkiro:text:m1:c1"),
                    ("ModelA", "cred-2", "xkiro:text:m1:c2"),
                    ("ModelB", "cred-1", "xkiro:text:m2:c1"),
                    ("ModelB", "cred-2", "xkiro:text:m2:c2"),
                ],
            )
        finally:
            for slot in slots:
                asyncio.run(slot.aclose())


if __name__ == "__main__":
    unittest.main()
