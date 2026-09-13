"""Regression coverage for configuration helpers that callers may rely on."""

from __future__ import annotations

import unittest

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
