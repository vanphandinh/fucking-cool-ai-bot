from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.ai.catalog import load_provider_catalog


class ProviderCatalogTests(unittest.TestCase):
    def test_catalog_loads_chainnode_and_xkiro(self) -> None:
        catalog = load_provider_catalog()
        self.assertEqual(tuple(catalog.providers), ("chainnode", "xkiro"))
        self.assertEqual(catalog.require("chainnode").driver, "openai-chat")
        self.assertEqual(catalog.require("xkiro").routes["vision"].max_images, 1)

    def test_catalog_rejects_duplicate_provider_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                """
[[providers]]
id = "dup"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[[providers]]
id = "dup"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate provider id"):
                load_provider_catalog(path)

    def test_catalog_rejects_unknown_driver_during_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                """
[[providers]]
id = "demo"
driver = "missing"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.text]
enabled = true
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown AI driver"):
                load_provider_catalog(path)

    def test_catalog_rejects_invalid_recovery_enum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.text]
enabled = true

[[providers.recovery]]
status = 429
action = "rotate_target"
scope = "unknown"
effect = "cooldown"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown recovery scope"):
                load_provider_catalog(path)

    def test_catalog_rejects_stop_as_provider_recovery_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.text]
enabled = true

[[providers.recovery]]
status = 418
action = "stop"
scope = "target"
effect = "record_only"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unsupported recovery action"):
                load_provider_catalog(path)

    def test_catalog_rejects_unknown_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.audio]
enabled = true
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown provider route"):
                load_provider_catalog(path)

    def test_catalog_rejects_invalid_vision_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(
                """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.vision]
enabled = true
supports_vision = true
max_images = 0
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "max_images"):
                load_provider_catalog(path)


    def test_catalog_rejects_unknown_fixed_schema_fields(self) -> None:
        cases = (
            (
                "provider",
                """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30
default_timeout_secs = 45

[providers.routes.text]
enabled = true
""",
                "default_timeout_secs",
            ),
            (
                "route",
                """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.text]
enable = false
""",
                "enable",
            ),
            (
                "recovery",
                """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.text]
enabled = true

[[providers.recovery]]
status = 429
action = "rotate_target"
scope = "model"
effect = "cooldown"
health_effect = "disable"
""",
                "health_effect",
            ),
        )
        for label, toml, unknown_field in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "providers.toml"
                path.write_text(toml, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, unknown_field):
                    load_provider_catalog(path)


    def test_catalog_rejects_non_boolean_route_flags(self) -> None:
        values = {
            "enabled": ("false", "true", "true"),
            "supports_vision": ("true", "false", "true"),
            "supports_tools": ("true", "true", "false"),
        }
        for field, (enabled, supports_vision, supports_tools) in values.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "providers.toml"
                rendered = f"""
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.vision]
enabled = {enabled}
supports_vision = {supports_vision}
supports_tools = {supports_tools}
max_images = 1
"""
                rendered = rendered.replace(
                    f"{field} = false",
                    f'{field} = "false"',
                )
                path.write_text(rendered, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, field):
                    load_provider_catalog(path)


if __name__ == "__main__":
    unittest.main()
