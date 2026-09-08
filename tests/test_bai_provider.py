"""Regression tests for the B.AI free-model provider integration."""

from __future__ import annotations

import importlib.util
from types import SimpleNamespace
import unittest

import app.ai.bai as bai


class BaiProviderModuleTests(unittest.TestCase):
    def test_bai_provider_module_exists(self) -> None:
        self.assertIsNotNone(importlib.util.find_spec("app.ai.bai"))

    def test_documented_zero_credit_models_are_explicit(self) -> None:
        self.assertEqual(
            bai.SUPPORTED_PROMO_MODELS,
            frozenset({"qwen3.8-flash", "mimo-v2.5", "hy3", "glm-5.3-flash"}),
        )

    def test_only_documented_multimodal_models_are_marked_for_vision(self) -> None:
        self.assertTrue(bai.model_supports_vision("qwen3.8-flash"))
        self.assertTrue(bai.model_supports_vision("mimo-v2.5"))
        self.assertTrue(bai.model_supports_vision("glm-5.3-flash"))
        self.assertFalse(bai.model_supports_vision("hy3"))
        self.assertFalse(bai.model_supports_vision("unknown"))

    def test_text_factory_uses_chat_completions_base_and_bai_timeout(self) -> None:
        factory = getattr(bai, "make_bai_provider", None)
        self.assertTrue(callable(factory))
        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="qwen3.8-flash",
            bai_vision_model="qwen3.8-flash",
            bai_request_timeout_sec=30.0,
        )
        provider = factory(settings)
        self.assertEqual(provider.name, "bai")
        self.assertEqual(provider.model, "qwen3.8-flash")
        self.assertEqual(str(provider._client.base_url), "https://api.b.ai/v1/")
        self.assertEqual(provider.capabilities.route, "text")
        self.assertFalse(provider.capabilities.supports_vision)
        self.assertEqual(provider.capabilities.max_images, 0)

    def test_factory_rejects_models_outside_documented_promotion(self) -> None:
        factory = getattr(bai, "make_bai_provider", None)
        self.assertTrue(callable(factory))
        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="not-promoted",
            bai_vision_model="qwen3.8-flash",
            bai_request_timeout_sec=30.0,
        )
        with self.assertRaisesRegex(ValueError, "B.AI model"):
            factory(settings)

    def test_vision_factory_rejects_text_only_hy3(self) -> None:
        factory = getattr(bai, "make_bai_provider", None)
        self.assertTrue(callable(factory))
        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="qwen3.8-flash",
            bai_vision_model="hy3",
            bai_request_timeout_sec=30.0,
        )
        with self.assertRaisesRegex(ValueError, "vision"):
            factory(settings, name="bai_vision", vision=True)

    def test_vision_factory_starts_with_one_image_capability(self) -> None:
        factory = getattr(bai, "make_bai_provider", None)
        self.assertTrue(callable(factory))
        settings = SimpleNamespace(
            bai_api_key="secret",
            bai_text_model="qwen3.8-flash",
            bai_vision_model="mimo-v2.5",
            bai_request_timeout_sec=30.0,
        )
        provider = factory(settings, name="bai_vision", vision=True)
        self.assertEqual(provider.model, "mimo-v2.5")
        self.assertEqual(provider.capabilities.route, "vision")
        self.assertTrue(provider.capabilities.supports_vision)
        self.assertEqual(provider.capabilities.max_images, 1)


if __name__ == "__main__":
    unittest.main()
