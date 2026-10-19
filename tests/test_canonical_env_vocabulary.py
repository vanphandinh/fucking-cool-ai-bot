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

    def test_generic_nested_provider_namespace_is_accepted(self) -> None:
        scanner = self._scanner()
        self.assertEqual(
            scanner.scan_text("AI_PROVIDERS__CHAINNODE__API_KEYS=secret\n"),
            (),
        )

    def test_scan_text_flags_plural_legacy_identifier_after_cleanup(self) -> None:
        scanner = self._scanner()
        retired = "CHAINNODE_API_" + "KEYS"
        self.assertEqual(scanner.scan_text(f"{retired}=secret\n"), (retired,))

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

    def test_captured_run_artifacts_are_skipped_but_prose_is_not(self) -> None:
        scanner = self._scanner()
        retired = "CHAINNODE_API_" + "KEYS"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "docs" / "acceptance" / "run_1.json"
            archived = root / "docs" / "benchmarks" / "archive" / "old.json"
            report = root / "docs" / "acceptance" / "EVALUATION.md"
            for target in (artifact, archived):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    f'{{"url": "https://example.test/{retired}"}}\n',
                    encoding="utf-8",
                )
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(f"{retired}=old\n", encoding="utf-8")
            with patch.object(
                scanner, "tracked_files", return_value=(artifact, archived, report)
            ):
                self.assertEqual(
                    scanner.scan_repository(root),
                    (f"{report.relative_to(root)}:1:{retired}",),
                )

    def test_json_outside_captured_dirs_is_still_scanned(self) -> None:
        scanner = self._scanner()
        retired = "XKIRO_API_" + "KEYS"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "docs" / "schema.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f'{{"key": "{retired}"}}\n', encoding="utf-8")
            with patch.object(scanner, "tracked_files", return_value=(target,)):
                self.assertEqual(
                    scanner.scan_repository(root),
                    (f"{target.relative_to(root)}:1:{retired}",),
                )


if __name__ == "__main__":
    unittest.main()
