from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from app.ai.runtime_config import ProviderRuntimeSettings, provider_runtime
from app.config import Settings


class ProviderEnvContractTests(unittest.TestCase):
    def test_nested_provider_env_is_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text(
                "\n".join(
                    [
                        "AI_PROVIDERS__CHAINNODE__API_KEYS=KeyOne,KeyTwo",
                        "AI_PROVIDERS__CHAINNODE__TEXT_MODELS=ModelA,ModelB",
                        "AI_PROVIDERS__CHAINNODE__VISION_MODELS=VisionA",
                        "AI_PROVIDERS__CHAINNODE__REQUEST_TIMEOUT_SEC=42",
                    ]
                ),
                encoding="utf-8",
            )
            settings = Settings(_env_file=env)
            runtime = provider_runtime(settings, "chainnode")
            self.assertEqual(runtime.api_keys_list, ["KeyOne", "KeyTwo"])
            self.assertEqual(runtime.text_models_list, ["ModelA", "ModelB"])
            self.assertEqual(runtime.request_timeout_sec, 42.0)

    def test_runtime_values_preserve_case_and_first_occurrence(self) -> None:
        runtime = ProviderRuntimeSettings(
            api_keys="KeyA,keya,KeyA",
            text_models="Model/Case,model/case,Model/Case",
        )
        self.assertEqual(runtime.api_keys_list, ["KeyA", "keya"])
        self.assertEqual(runtime.text_models_list, ["Model/Case", "model/case"])

    def test_provider_runtime_error_does_not_expose_api_key(self) -> None:
        secret = "SeCrEt-Provider-Key"
        with self.assertRaises(ValidationError) as ctx:
            ProviderRuntimeSettings(api_keys=secret, request_timeout_sec=-1)
        self.assertNotIn(secret, str(ctx.exception))

    def test_provider_ids_are_normalized_lowercase(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={"ChainNode": {"api_keys": "", "text_models": "ModelA"}},
        )
        self.assertIn("chainnode", settings.ai_providers)
        self.assertNotIn("ChainNode", settings.ai_providers)

    def test_unknown_provider_in_order_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unknown AI provider"):
            Settings(_env_file=None, text_provider_order="missing")

    def test_target_count_is_bounded_generically(self) -> None:
        models = ",".join(f"m{index}" for index in range(33))
        with self.assertRaisesRegex(ValidationError, "maximum is 64"):
            Settings(
                _env_file=None,
                ai_providers={
                    "chainnode": {
                        "api_keys": "k1,k2",
                        "text_models": models,
                    }
                },
                text_provider_order="chainnode",
                vision_enabled=False,
            )


if __name__ == "__main__":
    unittest.main()
