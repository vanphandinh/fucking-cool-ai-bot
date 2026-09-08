from pathlib import Path
import unittest


class SearXNGConfigTests(unittest.TestCase):
    def _settings_lines(self) -> list[str]:
        settings = Path(__file__).parents[1] / "searxng" / "settings.example.yml"
        return settings.read_text(encoding="utf-8").splitlines()

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
        text = "\n".join(self._settings_lines()).lower()
        self.assertNotIn("name: duckduckgo web", text)


if __name__ == "__main__":
    unittest.main()
