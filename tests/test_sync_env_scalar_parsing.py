"""Focused dotenv scalar parsing regressions for sync_env preflight checks."""

from __future__ import annotations

import unittest

from scripts import sync_env


class SyncEnvScalarParsingTests(unittest.TestCase):
    def test_unquoted_empty_value_before_comment_is_not_configured(self) -> None:
        values = {"CHAINNODE_API_KEY": " # intentionally empty"}

        self.assertEqual(
            sync_env._dotenv_scalar_value(
                values["CHAINNODE_API_KEY"],
                label="CHAINNODE_API_KEY",
            ),
            "",
        )
        self.assertFalse(sync_env._configured(values, "CHAINNODE_API_KEY"))

    def test_hash_without_preceding_whitespace_remains_literal_value(self) -> None:
        values = {"CHAINNODE_API_KEY": "#literal-value"}

        self.assertEqual(
            sync_env._dotenv_scalar_value(
                values["CHAINNODE_API_KEY"],
                label="CHAINNODE_API_KEY",
            ),
            "#literal-value",
        )
        self.assertTrue(sync_env._configured(values, "CHAINNODE_API_KEY"))


if __name__ == "__main__":
    unittest.main()
