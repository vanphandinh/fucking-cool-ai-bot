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

    def test_provider_retry_defaults(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.provider_retry_max_consecutive_failures, 2)
        self.assertEqual(settings.provider_retry_max_failures_per_provider, 3)
        self.assertEqual(settings.provider_retry_max_failures_per_request, 5)

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

        self.assertEqual(settings.provider_retry_max_consecutive_failures, 2)
        self.assertEqual(settings.provider_retry_max_failures_per_provider, 4)
        self.assertEqual(settings.provider_retry_max_failures_per_request, 6)

    @patch.dict(
        os.environ,
        {
            "PROVIDER_RETRY_MAX_CONSECUTIVE": "2",
            "PROVIDER_RETRY_MAX_PER_PROVIDER": "4",
            "PROVIDER_RETRY_MAX_PER_REQUEST": "6",
            "PROVIDER_RETRY_MAX_CONSECUTIVE_FAILURES": "1",
            "PROVIDER_RETRY_MAX_FAILURES_PER_PROVIDER": "3",
            "PROVIDER_RETRY_MAX_FAILURES_PER_REQUEST": "5",
        },
        clear=True,
    )
    def test_provider_retry_canonical_env_names_win_over_legacy_aliases(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.provider_retry_max_consecutive_failures, 2)
        self.assertEqual(settings.provider_retry_max_failures_per_provider, 4)
        self.assertEqual(settings.provider_retry_max_failures_per_request, 6)

    @patch.dict(
        os.environ,
        {
            "PROVIDER_RETRY_MAX_CONSECUTIVE_FAILURES": "2",
            "PROVIDER_RETRY_MAX_FAILURES_PER_PROVIDER": "4",
            "PROVIDER_RETRY_MAX_FAILURES_PER_REQUEST": "6",
        },
        clear=True,
    )
    def test_provider_retry_legacy_env_aliases_remain_supported(self) -> None:
        settings = Settings(_env_file=None)

        self.assertEqual(settings.provider_retry_max_consecutive_failures, 2)
        self.assertEqual(settings.provider_retry_max_failures_per_provider, 4)
        self.assertEqual(settings.provider_retry_max_failures_per_request, 6)

    def test_provider_retry_provider_budget_must_cover_consecutive_limit(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                provider_retry_max_consecutive_failures=3,
                provider_retry_max_failures_per_provider=2,
            )

    def test_provider_retry_request_budget_must_cover_consecutive_limit(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(
                _env_file=None,
                provider_retry_max_consecutive_failures=3,
                provider_retry_max_failures_per_request=2,
            )

    def test_provider_retry_ranges_are_bounded(self) -> None:
        invalid = (
            {"provider_retry_max_consecutive_failures": 0},
            {"provider_retry_max_consecutive_failures": 6},
            {"provider_retry_max_failures_per_provider": 0},
            {"provider_retry_max_failures_per_provider": 11},
            {"provider_retry_max_failures_per_request": 0},
            {"provider_retry_max_failures_per_request": 21},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    Settings(_env_file=None, **values)


if __name__ == "__main__":
    unittest.main()


class JobConfigTests(unittest.TestCase):
    def test_controls_defaults_and_validation(self):
        s = Settings(_env_file=None)
        self.assertFalse(s.question_controls_enabled)
        self.assertEqual(s.question_renewal_interval_sec, 180)
        for kw in ({'question_renewal_interval_sec': float('nan')},
                   {'question_max_inflight_operations': 17},
                   {'question_max_pending_jobs': 1, 'question_max_jobs_per_user': 2},
                   {'question_progress_interval_sec': 180},
                   {'tool_call_total_timeout_sec': 1}):
            with self.subTest(kw=kw), self.assertRaises(ValidationError):
                Settings(_env_file=None, **kw)

    def test_controlled_mode_independent_of_legacy_timeout(self):
        Settings(_env_file=None, question_controls_enabled=True,
                 question_timeout_sec=1, crawl4ai_api_token='test')
