"""Regression tests for the B.AI free-model provider integration."""

from __future__ import annotations

import importlib.util
import unittest


class BaiProviderModuleTests(unittest.TestCase):
    def test_bai_provider_module_exists(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec("app.ai.bai"),
            "B.AI provider module must exist before it can be routed",
        )


if __name__ == "__main__":
    unittest.main()
