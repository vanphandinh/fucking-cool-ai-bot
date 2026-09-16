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
            chainnode_api_key="ChainLegacyKey",
            chainnode_text_model="Legacy/ModelCase",
            xkiro_api_key="LegacyKeyCase",
            xkiro_text_model="LegacyX/Model",
            vision_enabled=False,
        )

        self.assertEqual(settings.chainnode_api_keys_list, ["ChainLegacyKey"])
        self.assertEqual(settings.chainnode_text_models_list, ["Legacy/ModelCase"])
        self.assertEqual(settings.xkiro_api_keys_list, ["LegacyKeyCase"])
        self.assertEqual(settings.xkiro_text_models_list, ["LegacyX/Model"])

    def test_explicit_plural_wins_and_preserves_case(self) -> None:
        settings = Settings(
            _env_file=None,
            chainnode_api_key="legacy-chain-key",
            chainnode_api_keys="ChainOne,chainTWO,ChainOne",
            chainnode_text_model="legacy",
            chainnode_text_models="Model/A,model/B,Model/A",
            xkiro_api_key="legacy-key",
            xkiro_api_keys="KeyOne,keyTWO",
            xkiro_text_model="legacy-model",
            xkiro_text_models="X/One,x/Two",
            vision_enabled=False,
        )

        self.assertEqual(settings.chainnode_api_keys_list, ["ChainOne", "chainTWO"])
        self.assertEqual(settings.chainnode_text_models_list, ["Model/A", "model/B"])
        self.assertEqual(settings.xkiro_api_keys_list, ["KeyOne", "keyTWO"])
        self.assertEqual(settings.xkiro_text_models_list, ["X/One", "x/Two"])

    def test_explicit_blank_plural_disables_scalar_fallback(self) -> None:
        settings = Settings(
            _env_file=None,
            chainnode_api_key="legacy-chain-key",
            chainnode_api_keys="",
            chainnode_text_model="legacy",
            chainnode_text_models="",
            xkiro_api_key="legacy-key",
            xkiro_api_keys="",
            xkiro_text_model="legacy-model",
            xkiro_text_models="",
            text_provider_order="",
            vision_provider_order="",
        )

        self.assertEqual(settings.chainnode_api_keys_list, [])
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

    def test_oversized_enabled_target_pool_is_rejected_before_factory_allocation(self) -> None:
        api_keys = ",".join(f"secret-{index}" for index in range(1, 10))
        models = ",".join(f"model-{index}" for index in range(1, 9))

        with self.assertRaises(ValidationError) as ctx:
            Settings(
                _env_file=None,
                xkiro_api_keys=api_keys,
                xkiro_text_models=models,
                text_provider_order="xkiro",
                vision_enabled=False,
            )

        error = str(ctx.exception)
        self.assertIn("72", error)
        self.assertIn("64", error)
        self.assertNotIn("secret-1", error)
        self.assertNotIn("secret-9", error)

    def test_oversized_chainnode_cartesian_pool_is_rejected_without_secret_leak(self) -> None:
        api_keys = ",".join(f"chain-secret-{index}" for index in range(1, 10))
        models = ",".join(f"chain-model-{index}" for index in range(1, 9))

        with self.assertRaises(ValidationError) as ctx:
            Settings(
                _env_file=None,
                chainnode_api_keys=api_keys,
                chainnode_text_models=models,
                text_provider_order="chainnode",
                vision_enabled=False,
            )

        error = str(ctx.exception)
        self.assertIn("72", error)
        self.assertIn("64", error)
        self.assertNotIn("chain-secret-1", error)
        self.assertNotIn("chain-secret-9", error)

    def test_mixed_provider_target_cap_counts_both_cartesian_pools(self) -> None:
        chain_keys = ",".join(f"chain-{index}" for index in range(1, 5))
        chain_models = ",".join(f"chain-model-{index}" for index in range(1, 10))
        xkiro_keys = ",".join(f"xkiro-{index}" for index in range(1, 5))
        xkiro_models = ",".join(f"xkiro-model-{index}" for index in range(1, 9))

        with self.assertRaises(ValidationError) as ctx:
            Settings(
                _env_file=None,
                chainnode_api_keys=chain_keys,
                chainnode_text_models=chain_models,
                xkiro_api_keys=xkiro_keys,
                xkiro_text_models=xkiro_models,
                text_provider_order="chainnode,xkiro",
                vision_enabled=False,
            )

        error = str(ctx.exception)
        self.assertIn("68", error)
        self.assertIn("64", error)
        self.assertNotIn("chain-1", error)
        self.assertNotIn("xkiro-1", error)

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
