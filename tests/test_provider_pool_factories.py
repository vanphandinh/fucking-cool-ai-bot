"""Generic target-pool ordering and capability regressions."""

from __future__ import annotations

import asyncio
import unittest

from app.ai.target_builder import build_provider_targets
from app.config import Settings


class ProviderPoolFactoryTests(unittest.TestCase):
    def _close(self, targets) -> None:
        for target in targets:
            asyncio.run(target.aclose())

    def test_chainnode_is_model_major_then_credential_for_each_route(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "chainnode": {
                    "api_keys": "ChainKeyOne,ChainKeyTwo",
                    "text_models": "DeepSeek/Case,GLM/Case",
                    "vision_models": "Vision/Case",
                }
            },
            text_provider_order="chainnode",
            vision_provider_order="chainnode",
        )
        targets = build_provider_targets(settings, ["chainnode"])
        try:
            self.assertEqual(
                [
                    (
                        target.capabilities.route,
                        target.model,
                        target.credential_id,
                        target.target_id,
                    )
                    for target in targets
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
        finally:
            self._close(targets)

    def test_xkiro_uses_same_generic_builder(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "xkiro": {
                    "api_keys": "KeyOne,KeyTwo",
                    "text_models": "ModelA",
                    "vision_models": "VisionA",
                }
            },
            text_provider_order="xkiro",
            vision_provider_order="xkiro",
        )
        targets = build_provider_targets(settings, ["xkiro"])
        try:
            self.assertEqual(
                [target.spec.identity.target_id for target in targets],
                [
                    "xkiro:text:m1:c1",
                    "xkiro:text:m1:c2",
                    "xkiro:vision:m1:c1",
                    "xkiro:vision:m1:c2",
                ],
            )
        finally:
            self._close(targets)


if __name__ == "__main__":
    unittest.main()
