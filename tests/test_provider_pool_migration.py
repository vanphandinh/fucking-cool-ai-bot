"""Provider-pool env migration regressions."""

from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.ai.xkiro import build_xkiro_provider_slots
from app.config import Settings


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "sync_env.py"


def _set_env_value(rendered: str, key: str, value: str) -> str:
    prefix = f"{key}="
    lines = rendered.splitlines()
    found = False
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix}{value}"
            found = True
            break
    if not found:
        raise AssertionError(f"missing {key} in synced env")
    return "\n".join(lines) + "\n"


def _assignment_values(rendered: str, keys: tuple[str, ...]) -> dict[str, str]:
    wanted = set(keys)
    values: dict[str, str] = {}
    for line in rendered.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in wanted:
            values[key] = value
    return values


class ProviderPoolMigrationTests(unittest.TestCase):
    def test_legacy_scalars_migrate_to_case_preserving_plural_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                "CHAINNODE_API_KEY=\n"
                "CHAINNODE_TEXT_MODELS=Default/Model\n"
                "CHAINNODE_TEXT_MODEL=Default/Model\n"
                "XKIRO_API_KEYS=\n"
                "XKIRO_TEXT_MODELS=\n"
                "XKIRO_API_KEY=\n"
                "XKIRO_TEXT_MODEL=\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )
            (directory / ".env").write_text(
                "CHAINNODE_API_KEY=ChainKeyCase\n"
                "CHAINNODE_TEXT_MODEL=Legacy/ModelCase\n"
                "XKIRO_API_KEY=KeyCaseSensitive\n"
                "XKIRO_TEXT_MODEL=X/ModelCase\n"
                "TEXT_PROVIDER_ORDER=chainnode,xkiro\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            rendered = (directory / ".env").read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CHAINNODE_TEXT_MODELS=Legacy/ModelCase", rendered)
        self.assertIn("XKIRO_API_KEYS=KeyCaseSensitive", rendered)
        self.assertIn("XKIRO_TEXT_MODELS=X/ModelCase", rendered)
        self.assertIn("CHAINNODE_TEXT_MODEL=Legacy/ModelCase", rendered)
        self.assertIn("XKIRO_API_KEY=KeyCaseSensitive", rendered)

    def test_env_sync_docs_legacy_scalar_block_matches_template(self) -> None:
        keys = (
            "CHAINNODE_TEXT_MODEL",
            "CHAINNODE_VISION_MODEL",
            "XKIRO_API_KEY",
            "XKIRO_TEXT_MODEL",
            "XKIRO_VISION_MODEL",
        )
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        docs = (ROOT / "docs" / "ENV_SYNC.md").read_text(encoding="utf-8")
        marker = "Legacy scalar fields remain in the template for backward compatibility:"
        self.assertIn(marker, docs)
        after_marker = docs.split(marker, 1)[1]
        self.assertIn("```env", after_marker)
        code_block = after_marker.split("```env", 1)[1].split("```", 1)[0]

        self.assertEqual(
            _assignment_values(code_block, keys),
            _assignment_values(template, keys),
        )

    def test_fresh_template_plural_xkiro_pool_is_operational_and_sync_is_idempotent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".env.example").write_text(
                (ROOT / ".env.example").read_text(encoding="utf-8"),
                encoding="utf-8",
            )

            first = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            env_path = directory / ".env"
            rendered = env_path.read_text(encoding="utf-8")
            rendered = _set_env_value(rendered, "XKIRO_API_KEYS", "KeyOne,KeyTwo")
            rendered = _set_env_value(rendered, "XKIRO_TEXT_MODELS", "ModelA,ModelB")
            rendered = _set_env_value(rendered, "XKIRO_VISION_MODELS", "VisionA")
            rendered = _set_env_value(rendered, "TEXT_PROVIDER_ORDER", "xkiro")
            rendered = _set_env_value(rendered, "VISION_PROVIDER_ORDER", "xkiro")
            env_path.write_text(rendered, encoding="utf-8")

            second = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            after_second = env_path.read_text(encoding="utf-8")
            third = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=directory,
                capture_output=True,
                text=True,
                check=False,
            )
            after_third = env_path.read_text(encoding="utf-8")

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(third.returncode, 0, third.stderr)
            self.assertEqual(after_second, after_third)

            settings = Settings(_env_file=env_path)
            slots = build_xkiro_provider_slots(settings)
            try:
                self.assertEqual(
                    [(slot.capabilities.route, slot.model, slot.credential_id) for slot in slots],
                    [
                        ("text", "ModelA", "cred-1"),
                        ("text", "ModelA", "cred-2"),
                        ("text", "ModelB", "cred-1"),
                        ("text", "ModelB", "cred-2"),
                        ("vision", "VisionA", "cred-1"),
                        ("vision", "VisionA", "cred-2"),
                    ],
                )
            finally:
                for slot in slots:
                    asyncio.run(slot.aclose())


if __name__ == "__main__":
    unittest.main()
