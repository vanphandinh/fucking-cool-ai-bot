"""Architecture boundaries for the generic AI provider runtime."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


_ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (_ROOT / relative_path).read_text(encoding="utf-8")


def _string_literals(relative_path: str) -> set[str]:
    tree = ast.parse(_source(relative_path), filename=relative_path)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


class AIArchitectureBoundaryTests(unittest.TestCase):
    def test_router_has_no_openai_wire_format_coupling(self) -> None:
        literals = _string_literals("app/ai/router.py")
        forbidden = {
            "tool_calls",
            "image_url",
            "chat/completions",
            "reasoning_content",
        }
        self.assertEqual(literals & forbidden, set())
        self.assertNotIn("_legacy_request", _source("app/ai/router.py"))

    def test_orchestrator_does_not_construct_openai_tool_calls(self) -> None:
        self.assertNotIn(
            "tool_calls",
            _string_literals("app/core/orchestrator.py"),
        )

    def test_recovery_policy_has_no_provider_family_branches(self) -> None:
        source = _source("app/ai/recovery.py").lower()
        self.assertNotIn("chainnode", source)
        self.assertNotIn("xkiro", source)

    def test_runtime_config_has_no_provider_specific_ai_settings(self) -> None:
        source = _source("app/config.py").lower()
        families = ("chain" + "node", "x" + "kiro")
        suffixes = (
            "api_" + "key",
            "api_" + "keys",
            "text_" + "model",
            "text_" + "models",
            "vision_" + "model",
            "vision_" + "models",
        )
        for family in families:
            for suffix in suffixes:
                self.assertNotIn(f"{family}_{suffix}", source)
        self.assertNotIn("X" + "KIRO_DEFAULT_BASE_URL", _source("app/config.py"))

    def test_openai_wire_vocabulary_is_driver_local(self) -> None:
        driver = _source("app/ai/drivers/openai_chat.py")
        for marker in (
            "chat/completions",
            "image_url",
            "reasoning_content",
            "tool_calls",
            "base64.b64encode",
        ):
            self.assertIn(marker, driver)

        self.assertNotIn(
            "image_url",
            _string_literals("app/ai/multimodal.py"),
        )

    def test_ai_runtime_has_no_direct_family_equality_branches(self) -> None:
        provider_specific = (
            'family == "chainnode"',
            'family == "xkiro"',
            "family == 'chainnode'",
            "family == 'xkiro'",
        )
        for path in (_ROOT / "app" / "ai").rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("identity.family ==", source, str(path))
            for marker in provider_specific:
                self.assertNotIn(marker, source, str(path))

    def test_ai_target_limit_has_one_runtime_config_source(self) -> None:
        runtime = _source("app/ai/runtime_config.py")
        config = _source("app/config.py")
        builder = _source("app/ai/target_builder.py")

        self.assertIn("MAX_AI_PROVIDER_TARGETS = 64", runtime)
        self.assertNotIn("_MAX_AI_PROVIDER_TARGETS = 64", config)
        self.assertNotIn("_MAX_PROVIDER_TARGETS = 64", builder)

    def test_production_image_packages_provider_catalog(self) -> None:
        dockerfile = _source("Dockerfile")
        self.assertIn("COPY config ./config", dockerfile)

    def test_legacy_compatibility_shims_are_removed(self) -> None:
        driver = _source("app/ai/drivers/openai_chat.py")
        router = _source("app/ai/router.py")
        base = _source("app/ai/base.py")

        self.assertNotIn("def _legacy_", driver)
        self.assertNotIn("ChatRequest | list[dict]", driver)
        self.assertNotIn("ChatRequest | list[dict]", router)
        self.assertNotIn("legacy_tool_executor", router)
        self.assertNotIn("ToolCall = ToolCallPart", base)
        self.assertNotIn("ChatResponse as ChatResponse", base)

        fake = _source("tests/provider_fakes.py")
        self.assertNotIn("_legacy_projection", fake)
        self.assertNotIn("self.calls = self.requests", fake)
        self.assertNotIn("fetch_url_tool", fake)
        self.assertNotIn("tool_calls", _string_literals("tests/provider_fakes.py"))


if __name__ == "__main__":
    unittest.main()
