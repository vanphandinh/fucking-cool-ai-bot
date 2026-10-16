"""The dependency lock must stay an exact, complete pin of the runtime set.

requirements.txt states ranges so a rebuild can silently pick newer code;
requirements.lock.txt is the set the offline suite was verified against, and both
the CI install step and the production image use it. These checks make drift
visible: a range bump without a lock update, a stray range inside the lock, or a
duplicate entry all fail here instead of surfacing as a surprise image diff.
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
import unittest
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).parents[1]
LOCK = ROOT / "requirements.lock.txt"
REQUIREMENTS = ROOT / "requirements.txt"
_PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;]+)$")


def _non_comment_lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _lock_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in _non_comment_lines(LOCK):
        match = _PIN_RE.match(line)
        if match is None:
            raise AssertionError(f"lock line is not an exact pin: {line!r}")
        name = match.group("name")
        if name.lower() in pins:
            raise AssertionError(f"duplicate lock entry: {name}")
        pins[name.lower()] = match.group("version")
    return pins


def _runtime_requirements() -> list[Requirement]:
    return [Requirement(line) for line in _non_comment_lines(REQUIREMENTS)]


class RequirementsLockTests(unittest.TestCase):
    def test_every_lock_line_is_an_exact_pin(self) -> None:
        pins = _lock_pins()
        self.assertGreater(len(pins), 0)
        for version in pins.values():
            Version(version)  # raises on a range, URL or garbage

    def test_lock_covers_every_runtime_requirement(self) -> None:
        pins = _lock_pins()
        for requirement in _runtime_requirements():
            with self.subTest(requirement=requirement.name):
                self.assertIn(
                    requirement.name.lower(),
                    pins,
                    "requirements.txt gained a dependency the lock does not pin",
                )

    def test_locked_versions_satisfy_the_declared_ranges(self) -> None:
        pins = _lock_pins()
        for requirement in _runtime_requirements():
            locked = pins.get(requirement.name.lower())
            if locked is None:
                continue
            with self.subTest(requirement=str(requirement)):
                self.assertIn(Version(locked), requirement.specifier)

    def test_lock_is_sorted_by_package_name_and_has_no_directives(self) -> None:
        lines = _non_comment_lines(LOCK)
        names = [_PIN_RE.match(line).group("name").lower() for line in lines]
        self.assertEqual(names, sorted(names))
        for line in lines:
            with self.subTest(line=line):
                self.assertFalse(line.startswith("-"), "lock must not include files or flags")

    def test_lock_does_not_carry_test_only_tools(self) -> None:
        pins = _lock_pins()
        for tool in ("ruff", "pip-audit", "markdown-it-py", "pyyaml"):
            with self.subTest(tool=tool):
                self.assertNotIn(tool, pins)

    def test_lock_is_closed_over_declared_dependencies(self) -> None:
        """A missing transitive dependency installs fine and fails ``pip check``.

        That is how ``pydantic-core`` was dropped from a first draft of this lock:
        the name differs from its metadata spelling, so a naive walk skipped it.
        """
        pins = _lock_pins()
        installed = {
            canonicalize_name(dist.metadata["Name"]): dist
            for dist in metadata.distributions()
            if dist.metadata.get("Name")
        }
        checked = 0
        for name, version in pins.items():
            dist = installed.get(name)
            if dist is None or dist.version != version:
                continue  # only a matching environment can prove the closure
            checked += 1
            for raw in dist.requires or []:
                requirement = Requirement(raw)
                if requirement.marker is not None and not requirement.marker.evaluate(
                    {"extra": ""}
                ):
                    continue
                with self.subTest(package=name, dependency=requirement.name):
                    self.assertIn(canonicalize_name(requirement.name), pins)
        self.assertGreater(checked, 0, "no locked version is installed to reconcile against")


if __name__ == "__main__":
    unittest.main()
