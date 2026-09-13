from pathlib import Path
import unittest


class SearXNGConfigTests(unittest.TestCase):
    def _settings_lines(self) -> list[str]:
        settings = Path(__file__).parents[1] / "searxng" / "settings.example.yml"
        return settings.read_text(encoding="utf-8").splitlines()

    def _settings_text(self) -> str:
        return "\n".join(self._settings_lines())

    def test_unstable_startpage_family_is_removed_by_exact_engine_name(self):
        removed = {
            line.strip()[2:]
            for line in self._settings_lines()
            if line.strip().startswith("- ")
        }
        expected = {"startpage", "startpage news", "startpage images"}
        self.assertTrue(
            expected.issubset(removed),
            f"missing Startpage removals: {sorted(expected - removed)}",
        )

    def test_broken_duckduckgo_html_engine_is_removed(self):
        removed = {
            line.strip()[2:]
            for line in self._settings_lines()
            if line.strip().startswith("- ")
        }
        self.assertIn("duckduckgo", removed)

    def test_duckduckgo_web_is_not_enabled_by_sample_config(self):
        self.assertNotIn("name: duckduckgo web", self._settings_text().lower())

    def test_engine_timeout_and_rate_limit_policy_is_explicit(self):
        text = self._settings_text()
        self.assertIn("request_timeout: 3.0", text)
        self.assertIn("max_request_timeout: 5.0", text)
        self.assertIn("SearxEngineTooManyRequests: 3600", text)
        self.assertIn("SearxEngineAccessDenied: 86400", text)
        self.assertIn("SearxEngineCaptcha: 86400", text)

    def test_wikidata_is_removed_to_avoid_late_timeout_results(self):
        removed = {
            line.strip()[2:]
            for line in self._settings_lines()
            if line.strip().startswith("- ")
        }
        self.assertIn("wikidata", removed)

    def test_default_searxng_image_is_pinned(self):
        compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertNotIn("searxng/searxng:latest", compose)
        self.assertRegex(
            compose,
            r"ghcr\.io/searxng/searxng:[0-9]{4}\.[0-9]+\.[0-9]+-[0-9a-f]{7,}",
        )

    def test_google_cse_web_and_image_timeout_are_overridden(self):
        text = self._settings_text()
        self.assertIn("- name: google cse\n    timeout: 5.0", text)
        self.assertIn("- name: google cse images\n    timeout: 5.0", text)

    def test_json_format_and_private_mode_remain_enabled(self):
        text = self._settings_text()
        self.assertIn("    - json", text)
        self.assertIn("limiter: false", text)
        self.assertIn("public_instance: false", text)


if __name__ == "__main__":
    unittest.main()
