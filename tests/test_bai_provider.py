"""Regression tests for the B.AI free-model provider integration."""

from __future__ import annotations

import importlib.util
import unittest

import app.ai.bai as bai


class BaiProviderModuleTests(unittest.TestCase):
    def test_bai_provider_module_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.ai.bai"))

    def test_documented_zero_credit_models_are_explicit(self) -> None:
        self.assertEqual(
            getattr(bai, "SUPPORTED_PROMO_MODELS", None),
            frozenset(
                {
                    "qwen3.8-flash",
                    "mimo-v2.5",
                    "hy3",
                    "glm-5.3-flash",
                }
            ),
        )

    def test_only_documented_multimodal_models_are_marked_for_vision(self) -> None:
        supports = getattr(bai, "model_supports_vision", None)
        self.assertTrue(callable(supports))
        self.assertTrue(supports("qwen3.8-flash"))
        self.assertTrue(supports("mimo-v2.5"))
        self.assertTrue(supports("glm-5.3-flash"))
        self.assertFalse(supports("hy3"))
        self.assertFalse(supports("unknown"))


if __name__ == "__main__":
    unittest.main()
