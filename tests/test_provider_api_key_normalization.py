"""Regression tests for provider API-key normalization at factory boundaries."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

import app.ai.chainnode as chainnode
import app.ai.xkiro as xkiro
from app.config import Settings


class ProviderApiKeyNormalizationTests(unittest.TestCase):
    def test_xkiro_whitespace_only_key_builds_no_slots(self) -> None:
        settings = Settings(
            _env_file=None,
            xkiro_api_key="   ",
            xkiro_text_model="test-model",
            text_provider_order="xkiro",
            vision_enabled=False,
        )
        self.assertEqual(xkiro.build_xkiro_provider_slots(settings), [])

    def test_chainnode_whitespace_only_key_builds_no_slots(self) -> None:
        settings = Settings(
            _env_file=None,
            chainnode_api_key="   ",
            chainnode_text_model="test-model",
            text_provider_order="chainnode",
            vision_enabled=False,
        )
        self.assertEqual(chainnode.build_chainnode_provider_slots(settings), [])

    def test_xkiro_factory_strips_api_key_before_authorization_header(self) -> None:
        settings = SimpleNamespace(
            xkiro_api_key="  test-key  ",
            xkiro_text_model="test-model",
            xkiro_vision_model="",
            xkiro_request_timeout_sec=30.0,
        )
        provider = xkiro.make_xkiro_provider(settings)
        try:
            self.assertEqual(provider._client.headers["Authorization"], "Bearer test-key")
        finally:
            asyncio.run(provider.aclose())

    def test_chainnode_factory_strips_api_key_before_authorization_header(self) -> None:
        settings = SimpleNamespace(
            chainnode_api_key="  test-key  ",
            chainnode_base_url="https://dn.chainno.de/v1",
            chainnode_text_model="test-model",
            chainnode_vision_model="",
            chainnode_request_timeout_sec=30.0,
        )
        provider = chainnode.make_chainnode_provider(settings)
        try:
            self.assertEqual(provider._client.headers["Authorization"], "Bearer test-key")
        finally:
            asyncio.run(provider.aclose())


if __name__ == "__main__":
    unittest.main()
