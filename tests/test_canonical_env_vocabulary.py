from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parents[1]
MODULE_NAME = "scripts.check_canonical_env_vocabulary"


class CanonicalEnvVocabularyTests(unittest.TestCase):
    def _scanner(self):
        spec = importlib.util.find_spec(MODULE_NAME)
        self.assertIsNotNone(spec, "canonical env vocabulary scanner must exist")
        return importlib.import_module(MODULE_NAME)

    def test_scan_text_flags_retired_identifier_at_identifier_boundaries(self) -> None:
        scanner = self._scanner()
        retired = "CHAINNODE_API_" + "KEY"
        self.assertEqual(scanner.scan_text(f"{retired}=secret\n"), (retired,))

    def test_scan_text_does_not_flag_plural_canonical_identifier(self) -> None:
        scanner = self._scanner()
        retired = "CHAINNODE_API_" + "KEY"
        canonical = retired + "S"
        self.assertEqual(scanner.scan_text(f"{canonical}=secret\n"), ())

    def test_scan_text_flags_previous_retry_attribute(self) -> None:
        scanner = self._scanner()
        retired = "provider_retry_max_consecutive_" + "failures"
        self.assertEqual(scanner.scan_text(f"value = settings.{retired}\n"), (retired,))

    def test_scan_text_flags_retired_xkiro_probe_credential_key(self) -> None:
        scanner = self._scanner()
        retired = "XKIRO_PROBE_API_" + "KEY"
        self.assertEqual(scanner.scan_text(f"{retired}=secret\n"), (retired,))

    def test_scan_text_flags_retired_provider_active_forms(self) -> None:
        scanner = self._scanner()
        cases = (
            "app.ai." + "b" + "ai",
            "build_" + "b" + "ai",
            "make_" + "b" + "ai",
            "B" + "AI_",
            "api.b." + "ai",
        )
        for retired in cases:
            with self.subTest(retired=retired):
                self.assertEqual(scanner.scan_text(retired), (retired,))

    def test_scan_text_flags_retired_provider_standalone_word(self) -> None:
        scanner = self._scanner()
        retired = "b" + "ai"
        self.assertEqual(scanner.scan_text(f"provider={retired}\n"), (retired,))

    def test_scan_repository_reports_path_line_and_identifier(self) -> None:
        scanner = self._scanner()
        retired = "XKIRO_TEXT_" + "MODEL"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sample.txt"
            target.write_text(f"ok\n{retired}=old\n", encoding="utf-8")
            with patch.object(scanner, "tracked_files", return_value=(target,)):
                self.assertEqual(
                    scanner.scan_repository(root),
                    (f"sample.txt:2:{retired}",),
                )

    def test_repository_has_no_retired_provider_or_env_identifiers(self) -> None:
        scanner = self._scanner()
        self.assertEqual(scanner.scan_repository(ROOT), ())


if __name__ == "__main__":
    unittest.main()
