from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.check_markdown_links import find_broken_links, scan_markdown_file


ROOT = Path(__file__).parents[1]


class MarkdownLinkCheckerTests(unittest.TestCase):
    def test_missing_relative_target_is_reported_with_source_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            guide = docs / "guide.md"
            guide.write_text(
                "# Guide\n\nSee [missing](missing.md).\n",
                encoding="utf-8",
            )

            self.assertEqual(
                scan_markdown_file(guide, root),
                ("docs/guide.md:3:missing.md",),
            )

    def test_missing_reference_target_is_reported_with_definition_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            guide = docs / "guide.md"
            guide.write_text(
                "# Guide\n\nSee [missing][target].\n\n[target]: missing.md\n",
                encoding="utf-8",
            )

            self.assertEqual(
                scan_markdown_file(guide, root),
                ("docs/guide.md:5:missing.md",),
            )

    def test_link_syntax_inside_inline_code_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            guide = docs / "guide.md"
            guide.write_text(
                "# Guide\n\nUse `[example](missing.md)` as syntax.\n",
                encoding="utf-8",
            )

            self.assertEqual(scan_markdown_file(guide, root), ())

    def test_four_space_indented_backticks_do_not_start_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            guide = docs / "guide.md"
            guide.write_text(
                "    ```\n[missing](missing.md)\n",
                encoding="utf-8",
            )

            self.assertEqual(
                scan_markdown_file(guide, root),
                ("docs/guide.md:2:missing.md",),
            )

    def test_shorter_fence_does_not_close_longer_fence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            guide = docs / "guide.md"
            guide.write_text(
                "````\n```\n[inside](missing.md)\n````\n",
                encoding="utf-8",
            )

            self.assertEqual(scan_markdown_file(guide, root), ())

    def test_balanced_parentheses_in_inline_destination_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            target = docs / "target(v2).md"
            target.write_text("# Target\n", encoding="utf-8")
            guide = docs / "guide.md"
            guide.write_text(
                "[target](target(v2).md)\n",
                encoding="utf-8",
            )

            self.assertEqual(scan_markdown_file(guide, root), ())

    def test_fence_with_trailing_text_does_not_close_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            guide = docs / "guide.md"
            guide.write_text(
                "```\n```not-a-close\n[inside](missing.md)\n```\n",
                encoding="utf-8",
            )

            self.assertEqual(scan_markdown_file(guide, root), ())

    def test_duplicate_reference_definition_uses_first_definition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            target = docs / "ok.md"
            target.write_text("# Target\n", encoding="utf-8")
            guide = docs / "guide.md"
            guide.write_text(
                "[x][id]\n\n[id]: ok.md\n[id]: missing.md\n",
                encoding="utf-8",
            )

            self.assertEqual(scan_markdown_file(guide, root), ())

    def test_existing_relative_targets_external_urls_and_anchors_are_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            target = docs / "target.md"
            target.write_text("# Target\n", encoding="utf-8")
            guide = docs / "guide.md"
            guide.write_text(
                "[local](target.md) [anchor](#section) "
                "[web](https://example.com) [mail](mailto:test@example.com)\n",
                encoding="utf-8",
            )

            self.assertEqual(scan_markdown_file(guide, root), ())

    def test_repository_has_no_broken_relative_markdown_links(self) -> None:
        self.assertEqual(find_broken_links(ROOT), ())


if __name__ == "__main__":
    unittest.main()
