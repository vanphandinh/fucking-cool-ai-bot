from pathlib import Path
import unittest


class SearXNGConfigTests(unittest.TestCase):
    def test_unstable_startpage_family_is_removed_by_exact_engine_name(self):
        settings = Path(__file__).parents[1] / "searxng" / "settings.example.yml"
        removed = {
            line.strip()[2:]
            for line in settings.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("- ")
        }
        expected = {"startpage", "startpage news", "startpage images"}
        self.assertTrue(
            expected.issubset(removed),
            f"missing Startpage removals: {sorted(expected - removed)}",
        )


if __name__ == "__main__":
    unittest.main()
