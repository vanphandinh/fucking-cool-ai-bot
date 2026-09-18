from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

import httpx

from app.ai.catalog import load_provider_catalog
from app.ai.contracts import ChatMessage, ChatRequest, TextPart, ToolDefinition
from app.ai.router import AIProviderRouter
from app.ai.target_builder import build_provider_targets
from app.config import Settings


class ProviderTargetBuilderTests(unittest.TestCase):
    def _close(self, targets) -> None:
        for target in targets:
            asyncio.run(target.aclose())

    def test_generic_builder_is_model_major_then_credential(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "chainnode": {
                    "api_keys": "KeyOne,KeyTwo",
                    "text_models": "ModelA,ModelB",
                    "vision_models": "VisionA",
                }
            },
            text_provider_order="chainnode",
            vision_provider_order="chainnode",
        )
        targets = build_provider_targets(settings, ["chainnode"])
        try:
            self.assertEqual(
                [
                    (
                        t.spec.identity.route,
                        t.spec.identity.model,
                        t.spec.identity.credential_id,
                        t.spec.identity.target_id,
                    )
                    for t in targets
                ],
                [
                    ("text", "ModelA", "cred-1", "chainnode:text:m1:c1"),
                    ("text", "ModelA", "cred-2", "chainnode:text:m1:c2"),
                    ("text", "ModelB", "cred-1", "chainnode:text:m2:c1"),
                    ("text", "ModelB", "cred-2", "chainnode:text:m2:c2"),
                    ("vision", "VisionA", "cred-1", "chainnode:vision:m1:c1"),
                    ("vision", "VisionA", "cred-2", "chainnode:vision:m1:c2"),
                ],
            )
        finally:
            self._close(targets)

    def test_no_credentials_builds_zero_targets(self) -> None:
        settings = Settings(_env_file=None, text_provider_order="chainnode")
        self.assertEqual(build_provider_targets(settings, ["chainnode"]), [])

    def test_provider_absent_from_route_order_builds_no_targets_for_that_route(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "chainnode": {
                    "api_keys": "Key",
                    "text_models": "Text",
                    "vision_models": "Vision",
                }
            },
            text_provider_order="chainnode",
            vision_provider_order="xkiro",
        )
        targets = build_provider_targets(settings, ["chainnode"])
        try:
            self.assertEqual([target.spec.identity.route for target in targets], ["text"])
        finally:
            self._close(targets)

    def test_unknown_provider_is_rejected_before_driver_construction(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown AI provider"):
            build_provider_targets(Settings(_env_file=None), ["missing"])

    def test_blank_base_url_uses_catalog_default_and_override_wins(self) -> None:
        for configured, expected in (
            ("", "https://dn.chainno.de/v1"),
            ("https://gateway.example/v1", "https://gateway.example/v1"),
        ):
            with self.subTest(configured=configured):
                settings = Settings(
                    _env_file=None,
                    ai_providers={
                        "chainnode": {
                            "api_keys": "Key",
                            "text_models": "Model",
                            "base_url": configured,
                        }
                    },
                    text_provider_order="chainnode",
                    vision_enabled=False,
                )
                targets = build_provider_targets(settings, ["chainnode"])
                try:
                    self.assertEqual(targets[0].spec.base_url, expected)
                finally:
                    self._close(targets)

    def test_builder_rejects_more_than_64_targets_after_settings_validation(self) -> None:
        settings = Settings(
            _env_file=None,
            ai_providers={
                "chainnode": {
                    "api_keys": "KeyOne",
                    "text_models": "OnlyValidatedModel",
                }
            },
            text_provider_order="chainnode",
            vision_enabled=False,
        )
        settings.ai_providers["chainnode"].text_models = ",".join(
            f"Model{index}" for index in range(65)
        )

        with self.assertRaisesRegex(ValueError, "64"):
            build_provider_targets(settings, ["chainnode"])

    def test_catalog_route_can_disable_tool_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                """
[[providers]]
id = "fixture"
driver = "openai-chat"
default_base_url = "https://fixture.example/v1"
default_timeout_sec = 15.0

[providers.routes.text]
enabled = true
supports_tools = false
""",
                encoding="utf-8",
            )
            catalog = load_provider_catalog(path)
            settings = Settings(
                _env_file=None,
                ai_providers={
                    "fixture": {"api_keys": "Key", "text_models": "FixtureModel"}
                },
                text_provider_order="",
                vision_provider_order="",
            )
            settings.text_provider_order = "fixture"
            targets = build_provider_targets(settings, ["fixture"], catalog=catalog)
            try:
                self.assertFalse(targets[0].spec.capabilities.supports_tools)
                self.assertFalse(targets[0].supports_tools)
            finally:
                self._close(targets)

    def test_catalog_only_openai_compatible_provider_needs_no_python_module(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                """
[[providers]]
id = "fixture"
driver = "openai-chat"
default_base_url = "https://fixture.example/v1"
default_timeout_sec = 15.0

[providers.routes.text]
enabled = true
""",
                encoding="utf-8",
            )
            catalog = load_provider_catalog(path)
            settings = Settings(
                _env_file=None,
                ai_providers={
                    "fixture": {"api_keys": "Key", "text_models": "FixtureModel"}
                },
                text_provider_order="",
                vision_provider_order="",
            )
            settings.text_provider_order = "fixture"
            targets = build_provider_targets(settings, ["fixture"], catalog=catalog)
            try:
                self.assertEqual(len(targets), 1)
                self.assertEqual(targets[0].name, "fixture")
                self.assertEqual(targets[0].spec.driver, "openai-chat")
            finally:
                self._close(targets)

class ConfigOnlyProviderIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_catalog_only_provider_routes_tools_and_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                """
[[providers]]
id = "fixturea"
driver = "openai-chat"
default_base_url = "https://fixture-a.example/v1"
default_timeout_sec = 15.0

[providers.routes.text]
enabled = true

[[providers]]
id = "fixtureb"
driver = "openai-chat"
default_base_url = "https://fixture-b.example/v1"
default_timeout_sec = 15.0

[providers.routes.text]
enabled = true
""",
                encoding="utf-8",
            )
            catalog = load_provider_catalog(path)
            settings = Settings(
                _env_file=None,
                ai_providers={
                    "fixturea": {"api_keys": "KeyA", "text_models": "ModelA"},
                    "fixtureb": {"api_keys": "KeyB", "text_models": "ModelB"},
                },
                text_provider_order="",
                vision_provider_order="",
            )
            settings.text_provider_order = "fixturea,fixtureb"
            targets = build_provider_targets(
                settings,
                ["fixturea", "fixtureb"],
                catalog=catalog,
            )

        primary, backup = targets
        for target in targets:
            await target.aclose()

        primary_attempts = 0
        backup_payloads: list[str] = []

        def primary_respond(request: httpx.Request) -> httpx.Response:
            nonlocal primary_attempts
            primary_attempts += 1
            if primary_attempts == 1:
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call_fetch",
                                            "type": "function",
                                            "function": {
                                                "name": "fetch_url",
                                                "arguments": (
                                                    '{"url":"https://example.com"}'
                                                ),
                                            },
                                        }
                                    ],
                                }
                            }
                        ]
                    },
                    request=request,
                )
            return httpx.Response(
                503,
                json={"error": {"message": "temporary outage"}},
                request=request,
            )

        def backup_respond(request: httpx.Request) -> httpx.Response:
            backup_payloads.append(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "fallback-ok",
                            }
                        }
                    ]
                },
                request=request,
            )

        primary._client = httpx.AsyncClient(
            base_url="https://fixture-a.example/v1/",
            transport=httpx.MockTransport(primary_respond),
        )
        backup._client = httpx.AsyncClient(
            base_url="https://fixture-b.example/v1/",
            transport=httpx.MockTransport(backup_respond),
        )
        router = AIProviderRouter(
            targets,
            text_provider_order=("fixturea", "fixtureb"),
        )
        executed: list[tuple[str, dict]] = []

        async def execute(name: str, args: dict) -> str:
            executed.append((name, args))
            return "EVIDENCE-FROM-TOOL"

        request = ChatRequest(
            messages=(ChatMessage("user", (TextPart("research"),)),),
            tools=(
                ToolDefinition(
                    name="fetch_url",
                    description="Fetch URL",
                    parameters={
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                ),
            ),
        )
        try:
            result = await router.complete(request, execute)
        finally:
            for target in targets:
                await target.aclose()

        self.assertEqual(result.provider, "fixtureb")
        self.assertEqual(result.fallbacks, ("fixtureb",))
        self.assertEqual(
            executed,
            [("fetch_url", {"url": "https://example.com"})],
        )
        self.assertEqual(primary_attempts, 2)
        self.assertEqual(len(backup_payloads), 1)
        self.assertIn("EVIDENCE-FROM-TOOL", backup_payloads[0])


if __name__ == "__main__":
    unittest.main()
