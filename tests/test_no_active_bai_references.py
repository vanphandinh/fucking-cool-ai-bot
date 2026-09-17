"""Dead-reference gate for the retired B.AI provider."""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).parents[1]
ALLOWED_LEGACY_FIXTURE_FILES = {
    Path(__file__),
    ROOT / "tests" / "test_chainnode_multikey_migration_preflight.py",
    ROOT / "tests" / "test_provider_env_migration.py",
    ROOT / "tests" / "test_sync_env.py",
}
ALLOWED_LEGACY_LITERAL_FILES = {
    ROOT / "scripts" / "sync_env.py",
}
ACTIVE_PATTERN = re.compile(
    r"app\.ai\.bai|build_bai|make_bai|BAI_|api\.b\.ai",
    re.IGNORECASE,
)
LEGACY_LITERAL_PATTERN = re.compile(r"\bbai\b", re.IGNORECASE)


class NoActiveBaiReferencesTests(unittest.TestCase):
    def test_bai_is_absent_outside_historical_docs_and_migration_fixtures(self) -> None:
        roots = [
            ROOT / "app",
            ROOT / "scripts",
            ROOT / "tests",
            ROOT / "README.md",
            ROOT / ".env.example",
            ROOT / "docs",
        ]
        hits: list[str] = []
        for root in roots:
            paths = [root] if root.is_file() else root.rglob("*")
            for path in paths:
                if not path.is_file() or path.suffix in {".pyc", ".png", ".jpg", ".zip"}:
                    continue
                if "docs/superpowers" in path.as_posix():
                    continue
                if path in ALLOWED_LEGACY_FIXTURE_FILES:
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    has_active_reference = ACTIVE_PATTERN.search(line) is not None
                    has_unallowed_legacy_literal = (
                        LEGACY_LITERAL_PATTERN.search(line) is not None
                        and path not in ALLOWED_LEGACY_LITERAL_FILES
                    )
                    if has_active_reference or has_unallowed_legacy_literal:
                        hits.append(f"{path.relative_to(ROOT)}:{line_no}: {line.strip()}")
        self.assertEqual(hits, [], "active B.AI references remain:\n" + "\n".join(hits))


if __name__ == "__main__":
    unittest.main()
