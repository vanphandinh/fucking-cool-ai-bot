"""Regression coverage for configuration helpers that callers may rely on."""

from __future__ import annotations

import unittest

from app.config import Settings


class ConfigRegressionTests(unittest.TestCase):
    def test_bot_username_clean_remains_available_and_normalized(self) -> None:
        settings = Settings(_env_file=None, bot_username="@MyBot")

        self.assertTrue(hasattr(settings, "bot_username_clean"))
        self.assertEqual(settings.bot_username_clean, "mybot")


if __name__ == "__main__":
    unittest.main()
