"""Generic provider-pool configuration contracts."""

from __future__ import annotations

import asyncio
import unittest

from pydantic import ValidationError

from app.ai.runtime_config import provider_runtime
from app.ai.target_builder import build_provider_targets
from app.config import Settings


class ProviderPoolConfigTests(unittest.TestCase):
    def test_pools_preserve_case_and_deduplicate(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "chainnode": {
                    "api_keys": "ChainOne,chainTWO,ChainOne",
                    "text_models": "Model/A,model/B,Model/A",
                },
                "xkiro": {
                    "api_keys": "KeyOne,keyTWO",
                    "text_models": "X/One,x/Two",
                },
            },
            vision_enabled=False,
        )
        self.assertEqual(
            provider_runtime(settings, "chainnode").api_keys_list,
            ["ChainOne", "chainTWO"],
        )
        self.assertEqual(
            provider_runtime(settings, "chainnode").text_models_list,
            ["Model/A", "model/B"],
        )
        self.assertEqual(
            provider_runtime(settings, "xkiro").api_keys_list,
            ["KeyOne", "keyTWO"],
        )

    def test_blank_pool_is_empty(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "chainnode": {
                    "api_keys": "",
                    "text_models": "",
                }
            },
            text_provider_order="",
            vision_provider_order="",
        )
        self.assertEqual(provider_runtime(settings, "chainnode").api_keys_list, [])

    def test_recovery_hop_limit_is_bounded(self) -> None:
        self.assertEqual(
            Settings(_env_file=None).provider_recovery_max_hops_per_request,
            5,
        )
        for invalid in (0, 21):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                Settings(
                    _env_file=None,
                    provider_recovery_max_hops_per_request=invalid,
                )

    def test_oversized_cartesian_pool_is_rejected_without_secret_leak(self) -> None:
        keys = ",".join(f"secret-{index}" for index in range(1, 10))
        models = ",".join(f"model-{index}" for index in range(1, 9))
        with self.assertRaises(ValidationError) as ctx:
            Settings(
                _env_file=None,
                ai_providers={
                    "xkiro": {
                        "api_keys": keys,
                        "text_models": models,
                    }
                },
                text_provider_order="xkiro",
                vision_enabled=False,
            )
        rendered = str(ctx.exception)
        self.assertIn("72", rendered)
        self.assertIn("64", rendered)
        self.assertNotIn("secret-1", rendered)

    def test_generic_builder_is_model_major_then_credential(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "xkiro": {
                    "api_keys": "KeyOne,KeyTwo",
                    "text_models": "ModelA,ModelB",
                }
            },
            text_provider_order="xkiro",
            vision_enabled=False,
        )
        targets = build_provider_targets(settings, ["xkiro"])
        try:
            self.assertEqual(
                [
                    (target.model, target.credential_id, target.target_id)
                    for target in targets
                ],
                [
                    ("ModelA", "cred-1", "xkiro:text:m1:c1"),
                    ("ModelA", "cred-2", "xkiro:text:m1:c2"),
                    ("ModelB", "cred-1", "xkiro:text:m2:c1"),
                    ("ModelB", "cred-2", "xkiro:text:m2:c2"),
                ],
            )
        finally:
            for target in targets:
                asyncio.run(target.aclose())


if __name__ == "__main__":
    unittest.main()
