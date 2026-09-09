"""Canonical offline regression launcher.

The repository keeps behavior tests in ``tests/test_*.py``. This script exists
for the historical ``python tests/run_tests.py`` CI entrypoint and deliberately
runs the same structured unittest suite instead of maintaining a second,
divergent manual harness.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).parents[1]
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT))


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(TESTS), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
