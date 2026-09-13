from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


class SearXNGSettingsGuardTests(unittest.TestCase):
    def _load_guard(self):
        root = Path(__file__).parents[1]
        script = root / "scripts" / "check_searxng_settings.py"
        self.assertTrue(script.is_file(), "missing runtime SearXNG settings guard")
        spec = spec_from_file_location("check_searxng_settings", script)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_guard_accepts_current_policy_and_rejects_stale_production_values(self):
        root = Path(__file__).parents[1]
        sample = (root / "searxng" / "settings.example.yml").read_text(encoding="utf-8")
        good = sample.replace(
            "REPLACE_WITH_openssl_rand_-hex_32",
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )
        guard = self._load_guard()
        self.assertEqual([], guard.validate_settings_text(good))

        stale = good.replace(
            "SearxEngineTooManyRequests: 3600",
            "SearxEngineTooManyRequests: 180",
        ).replace("      - wikidata\n", "")
        errors = guard.validate_settings_text(stale)
        self.assertTrue(any("SearxEngineTooManyRequests" in error for error in errors))
        self.assertTrue(any("wikidata" in error for error in errors))

    def test_guard_rejects_placeholder_secret(self):
        root = Path(__file__).parents[1]
        sample = (root / "searxng" / "settings.example.yml").read_text(encoding="utf-8")
        guard = self._load_guard()
        errors = guard.validate_settings_text(sample)
        self.assertTrue(any("secret_key" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
