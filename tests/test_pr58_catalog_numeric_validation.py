"""PR #58 regressions for strict provider-catalog numeric validation."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.ai.catalog import load_provider_catalog


class ProviderCatalogNumericValidationTests(unittest.TestCase):
    def _load(self, body: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "providers.toml"
            path.write_text(body, encoding="utf-8")
            return load_provider_catalog(path)

    def test_route_max_images_rejects_non_integer_toml_number(self) -> None:
        body = """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.vision]
enabled = true
supports_vision = true
max_images = 1.9
"""
        with self.assertRaisesRegex(ValueError, "max_images.*integer"):
            self._load(body)

    def test_default_timeout_rejects_boolean_and_string_coercions(self) -> None:
        for raw_timeout in ("true", '"30"'):
            with self.subTest(raw_timeout=raw_timeout):
                body = f"""
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = {raw_timeout}

[providers.routes.text]
enabled = true
"""
                with self.assertRaisesRegex(ValueError, "timeout.*number"):
                    self._load(body)

    def test_recovery_status_rejects_non_integer_toml_number(self) -> None:
        body = """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.text]
enabled = true

[[providers.recovery]]
status = 429.5
action = "rotate_target"
scope = "credential"
effect = "cooldown"
"""
        with self.assertRaisesRegex(ValueError, "recovery status.*integer"):
            self._load(body)

    def test_recovery_status_rejects_out_of_http_range(self) -> None:
        body = """
[[providers]]
id = "demo"
driver = "openai-chat"
default_base_url = "https://example.test/v1"
default_timeout_sec = 30

[providers.routes.text]
enabled = true

[[providers.recovery]]
status = 42
action = "rotate_target"
scope = "credential"
effect = "cooldown"
"""
        with self.assertRaisesRegex(ValueError, "recovery status.*100.*599"):
            self._load(body)


if __name__ == "__main__":
    unittest.main()
