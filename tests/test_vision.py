"""Regression tests for Telegram multimodal/vision support."""

from __future__ import annotations

import importlib.util
import unittest


class VisionRequestRedTests(unittest.TestCase):
    def test_canonical_request_module_exists(self) -> None:
        self.assertIsNotNone(
            importlib.util.find_spec("app.core.request"),
            "app.core.request must define the provider-independent multimodal request model",
        )


if __name__ == "__main__":
    unittest.main()
