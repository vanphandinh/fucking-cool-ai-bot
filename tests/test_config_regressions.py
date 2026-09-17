"""Regression coverage for configuration helpers that callers may rely on."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.config import Settings


class ConfigRegressionTests(unittest.TestCase):
    def test_bot_username_clean_remains_available_and_normalized(self) -> None:
        settings = Settings(_env_file=None, bot_username="@MyBot")

        self.assertTrue(hasattr(settings, "bot_username_clean"))
        self.assertEqual(settings.bot_username_clean, "mybot")

    def test_provider_defaults_make_chainnode_primary_and_xkiro_fallback(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.text_provider_order_list, ["chainnode", "xkiro"])
        self.assertEqual(settings.vision_provider_order_list, ["chainnode", "xkiro"])
        self.assertEqual(settings.xkiro_api_keys_list, [])
        self.assertEqual(settings.xkiro_base_url, "https://api.xkiro.com/v1")
        self.assertEqual(settings.xkiro_text_models_list, [])
        self.assertEqual(settings.xkiro_vision_models_list, [])
        self.assertEqual(settings.xkiro_request_timeout_sec, 60.0)

    def test_xkiro_text_order_requires_explicit_model_when_key_is_configured(self) -> None:
        with self.assertRaisesRegex(ValidationError, "XKIRO_TEXT_MODELS"):
            Settings(
                _env_file=None,
                xkiro_api_keys="test-key",
                text_provider_order="chainnode,xkiro",
                xkiro_text_models="",
            )

    def test_xkiro_vision_order_requires_explicit_model_when_enabled(self) -> None:
        with self.assertRaisesRegex(ValidationError, "XKIRO_VISION_MODELS"):
            Settings(
                _env_file=None,
                xkiro_api_keys="test-key",
                text_provider_order="chainnode",
                vision_enabled=True,
                vision_provider_order="chainnode,xkiro",
                xkiro_vision_models="",
            )

    def test_disabled_vision_does_not_require_xkiro_vision_model(self) -> None:
        settings = Settings(
            _env_file=None,
            xkiro_api_keys="test-key",
            text_provider_order="chainnode",
            vision_enabled=False,
            vision_provider_order="chainnode,xkiro",
            xkiro_vision_models="",
        )
        self.assertFalse(settings.vision_enabled)

    def test_provider_retry_settings_use_canonical_python_names(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.provider_retry_max_consecutive, 2)
        self.assertEqual(settings.provider_retry_max_per_provider, 3)
        self.assertEqual(settings.provider_retry_max_per_request, 5)
        for field_name in (
            "provider_retry_max_consecutive",
            "provider_retry_max_per_provider",
            "provider_retry_max_per_request",
        ):
            self.assertIsNone(Settings.model_fields[field_name].validation_alias)

    @patch.dict(
        os.environ,
        {
            "PROVIDER_RETRY_MAX_CONSECUTIVE": "2",
            "PROVIDER_RETRY_MAX_PER_PROVIDER": "4",
            "PROVIDER_RETRY_MAX_PER_REQUEST": "6",
        },
        clear=True,
    )
    def test_provider_retry_canonical_env_names_are_supported(self) -> None:
        settings = Settings(_env_file=None)
        self.assertEqual(settings.provider_retry_max_consecutive, 2)
        self.assertEqual(settings.provider_retry_max_per_provider, 4)
        self.assertEqual(settings.provider_retry_max_per_request, 6)

    def test_provider_retry_provider_budget_must_cover_consecutive_limit(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                provider_retry_max_consecutive=3,
                provider_retry_max_per_provider=2,
            )

    def test_provider_retry_request_budget_must_cover_consecutive_limit(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                provider_retry_max_consecutive=3,
                provider_retry_max_per_request=2,
            )

    def test_provider_retry_ranges_are_bounded(self) -> None:
        invalid = (
            {"provider_retry_max_consecutive": 0},
            {"provider_retry_max_consecutive": 6},
            {"provider_retry_max_per_provider": 0},
            {"provider_retry_max_per_provider": 11},
            {"provider_retry_max_per_request": 0},
            {"provider_retry_max_per_request": 21},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    Settings(_env_file=None, **values)


class JobConfigTests(unittest.TestCase):
    def test_controls_defaults_and_validation(self):
        s = Settings(_env_file=None)
        self.assertFalse(s.question_controls_enabled)
        self.assertEqual(s.question_renewal_interval_sec, 180)
        for kw in (
            {"question_renewal_interval_sec": float("nan")},
            {"question_max_inflight_operations": 17},
            {"question_max_pending_jobs": 1, "question_max_jobs_per_user": 2},
            {"question_progress_interval_sec": 180},
            {"tool_call_total_timeout_sec": 1},
        ):
            with self.subTest(kw=kw), self.assertRaises(ValidationError):
                Settings(_env_file=None, **kw)

    def test_controlled_mode_independent_of_legacy_timeout(self):
        Settings(
            _env_file=None,
            question_controls_enabled=True,
            question_timeout_sec=1,
            crawl4ai_api_token="test-token",
        )


if __name__ == "__main__":
    unittest.main()
