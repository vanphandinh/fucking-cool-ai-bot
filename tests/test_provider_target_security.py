"""Security regressions for provider target identities and diagnostics."""

from __future__ import annotations

import asyncio
import unittest

from pydantic import ValidationError

from app.ai.base import AllProvidersFailed, ProviderError
from app.ai.router import AIProviderRouter
from app.ai.target_builder import build_provider_targets
from app.ai.target import provider_target_identity
from app.bot.handlers import _provider_status_lines
from app.config import Settings
from app.core.stats import Stats
from tests.provider_fakes import ScriptedProvider, noop_tool, text_request


class ProviderTargetIdentitySecurityTests(unittest.TestCase):
    def test_provider_identity_comes_from_target_spec(self) -> None:
        provider = ScriptedProvider("demo", [])
        self.assertEqual(provider_target_identity(provider), provider.spec.identity)

    def test_target_spec_repr_never_contains_secret(self) -> None:
        secret = "SeCrEt-Value"
        provider = ScriptedProvider("demo", [], credential=secret)
        rendered = repr(provider.spec) + repr(provider_target_identity(provider))
        self.assertNotIn(secret, rendered)
        self.assertIn("cred-1", rendered)

    def test_xkiro_target_identity_never_contains_raw_api_key(self) -> None:
        secrets = ("SeCrEt-Key-One", "SeCrEt-Key-Two")
        settings = Settings(
            _env_file=None,
            ai_providers={"xkiro":{"api_keys":",".join(secrets),"text_models":"Model/Case"}},
            text_provider_order="xkiro",
            vision_enabled=False,
        )
        slots = build_provider_targets(settings, ["xkiro"])
        try:
            rendered = repr([provider_target_identity(slot) for slot in slots])
            rendered += repr([slot.target_id for slot in slots])
            rendered += repr(slots)
            for secret in secrets:
                self.assertNotIn(secret, rendered)
            self.assertIn("cred-1", rendered)
            self.assertIn("cred-2", rendered)
        finally:
            for slot in slots:
                asyncio.run(slot.aclose())

    def test_chainnode_target_identity_never_contains_raw_api_key(self) -> None:
        secrets = ("Chain-SeCrEt-One", "Chain-SeCrEt-Two")
        settings = Settings(
            _env_file=None,
            ai_providers={"chainnode":{"api_keys":",".join(secrets),"text_models":"Model/Case"}},
            text_provider_order="chainnode",
            vision_enabled=False,
        )
        slots = build_provider_targets(settings, ["chainnode"])
        try:
            rendered = repr([provider_target_identity(slot) for slot in slots])
            rendered += repr([slot.target_id for slot in slots])
            rendered += repr(slots)
            for secret in secrets:
                self.assertNotIn(secret, rendered)
            self.assertEqual([slot.credential_id for slot in slots], ["cred-1", "cred-2"])
        finally:
            for slot in slots:
                asyncio.run(slot.aclose())


class SettingsSecretHygieneTests(unittest.TestCase):
    def test_validation_error_hides_canonical_xkiro_credentials(self) -> None:
        secrets = (
            "".join(("A1", "!x")),
            "".join(("B2", "!y")),
        )

        with self.assertRaises(ValidationError) as ctx:
            Settings(
                _env_file=None,
                ai_providers={"xkiro":{"api_keys":",".join(secrets),"text_models":""}},
                text_provider_order="xkiro",
                vision_enabled=False,
            )

        rendered = str(ctx.exception)
        if any(secret in rendered for secret in secrets):
            self.fail("validation error exposed raw xKiro credential input")
        self.assertIn("AI_PROVIDERS__XKIRO__TEXT_MODELS", rendered)
        retired = "XKIRO_TEXT_" + "MODEL"
        self.assertNotIn(f"{retired} là", rendered)

    def test_validation_error_hides_canonical_chainnode_credentials(self) -> None:
        secrets = (
            "".join(("CN-A1", "!x")),
            "".join(("CN-B2", "!y")),
        )

        with self.assertRaises(ValidationError) as ctx:
            Settings(
                _env_file=None,
                ai_providers={"chainnode":{"api_keys":",".join(secrets),"text_models":""}},
                text_provider_order="chainnode",
                vision_enabled=False,
            )

        rendered = str(ctx.exception)
        if any(secret in rendered for secret in secrets):
            self.fail("validation error exposed raw Chainnode credential input")
        self.assertIn("AI_PROVIDERS__CHAINNODE__TEXT_MODELS", rendered)
        retired = "CHAINNODE_TEXT_" + "MODEL"
        self.assertNotIn(f"{retired} là", rendered)


class ProviderTargetDiagnosticSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_raw_key_is_absent_from_logs_status_exception_and_repr(self) -> None:
        secret = "SeCrEt-Diagnostic-Key"
        provider = ScriptedProvider(
            "xkiro",
            [ProviderError("quota", status_code=429, transient=True)],
        )
        provider.model = "Model/Case"
        provider.credential_id = "cred-1"
        provider.target_id = "xkiro:text:m1:c1"
        provider.api_key = secret
        router = AIProviderRouter(
            [provider],
            text_provider_order=("xkiro",),
            vision_provider_order=(),
        )

        with self.assertLogs("app.ai.router", level="WARNING") as captured:
            with self.assertRaises(AllProvidersFailed) as ctx:
                await router.complete(text_request("hello"), noop_tool)

        rendered = "\n".join(
            (
                repr(provider_target_identity(provider)),
                repr(provider),
                str(ctx.exception),
                *captured.output,
                *_provider_status_lines(router, Stats()),
            )
        )
        self.assertNotIn(secret, rendered)
        self.assertIn("xkiro:text:m1:c1", rendered)


if __name__ == "__main__":
    unittest.main()
